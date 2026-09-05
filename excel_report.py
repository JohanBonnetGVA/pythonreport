"""Excel workbook construction: sheets, styling, and charts.

Pure presentation layer — takes a `ScenarioResult` plus the annotated peer
fact table and renders a single .xlsx file. No business logic here (see
`pricing_logic.py`); no I/O other than reading `theme.json` and writing the
output workbook.

Sheets (SPEC.md §7):
  Dashboard  - KPI summary + one whisker chart per fee category
  Input      - banker enters the client id and the promised fee per category
  RawData    - full peer fact table as a native Excel Table
  Config     - AUM bracket edges (editable) and category list (reference)
  Stats      - hidden helper sheet: peer stats + chart point data + the
               formulas that place the "current"/"promised" markers
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import Reference, ScatterChart, Series
from openpyxl.chart.marker import Marker
from openpyxl.styles import Font, PatternFill, Protection
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from data_extraction import Category
from pricing_logic import BRACKETS, PeerStats, ScenarioResult

logger = logging.getLogger(__name__)

THEME_PATH = Path(__file__).with_name("theme.json")

NA = "=NA()"
HALF_WIDTH = 0.15  # half box/median-tick width, in bracket-index units

CATEGORIES = list(Category)
BRACKET_LABELS = [b.label for b in BRACKETS]


@dataclass
class Theme:
    colors: dict[str, str]
    fonts: dict[str, object]
    number_formats: dict[str, str]

    @classmethod
    def load(cls, path: Path = THEME_PATH) -> "Theme":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(colors=data["colors"], fonts=data["fonts"], number_formats=data["number_formats"])

    def font(self, *, size_key: str = "size_body", bold: bool = False, color: str | None = None) -> Font:
        return Font(
            name=self.fonts["family"],
            size=self.fonts[size_key],
            bold=bold,
            color=color,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_workbook(
    scenario: ScenarioResult,
    peer_df: pd.DataFrame,
    output_path: str,
    theme_path: Path | None = None,
) -> None:
    theme = Theme.load(theme_path or THEME_PATH)

    wb = Workbook()
    ws_config = wb.active
    ws_config.title = "Config"
    _write_config_sheet(ws_config, theme)

    ws_rawdata = wb.create_sheet("RawData")
    _write_rawdata_sheet(ws_rawdata, theme, peer_df)

    ws_input = wb.create_sheet("Input")
    _write_input_sheet(ws_input, theme, scenario, peer_df)

    ws_stats = wb.create_sheet("Stats")
    _write_stats_sheet(ws_stats, theme, peer_df)
    ws_stats.sheet_state = "hidden"

    ws_dashboard = wb.create_sheet("Dashboard")
    _write_dashboard_sheet(ws_dashboard, theme, scenario, ws_stats)

    wb._sheets = [ws_dashboard, ws_input, ws_rawdata, ws_config, ws_stats]
    wb.active = 0

    wb.save(output_path)
    logger.info("Workbook written to %s", output_path)


# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------

def _style_header_row(ws, row: int, n_cols: int, theme: Theme) -> None:
    fill = PatternFill("solid", fgColor=theme.colors["primary"])
    font = theme.font(size_key="size_header", bold=True, color=theme.colors["header_text"])
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = fill
        cell.font = font


def _title(ws, cell: str, text: str, theme: Theme) -> None:
    ws[cell] = text
    ws[cell].font = theme.font(size_key="size_title", bold=True, color=theme.colors["primary"])


def _unlock(cell) -> None:
    cell.protection = Protection(locked=False)


def _highlight_input(cell, theme: Theme) -> None:
    cell.fill = PatternFill("solid", fgColor=theme.colors["input_highlight"])
    _unlock(cell)


def _protect_sheet(ws) -> None:
    ws.protection.sheet = True
    ws.protection.formatCells = False
    ws.protection.formatColumns = False
    ws.protection.formatRows = False


def _autosize(ws, widths: dict[str, int]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[col].width = width


# ---------------------------------------------------------------------------
# Config sheet
# ---------------------------------------------------------------------------

CONFIG_BRACKET_HEADER_ROW = 1
CONFIG_BRACKET_FIRST_ROW = 2
CONFIG_BRACKET_LAST_ROW = CONFIG_BRACKET_FIRST_ROW + len(BRACKETS) - 1


def _write_config_sheet(ws, theme: Theme) -> None:
    ws.append(["AUM Bracket", "Lower bound (incl.)", "Upper bound (excl.)"])
    _style_header_row(ws, CONFIG_BRACKET_HEADER_ROW, 3, theme)
    for bracket in BRACKETS:
        high = bracket.high if bracket.high != float("inf") else None
        row = [bracket.label, bracket.low, high]
        ws.append(row)
        r = ws.max_row
        _unlock(ws.cell(row=r, column=2))
        _unlock(ws.cell(row=r, column=3))
        ws.cell(row=r, column=2).number_format = theme.number_formats["currency"]
        ws.cell(row=r, column=3).number_format = theme.number_formats["currency"]

    cat_header_row = CONFIG_BRACKET_LAST_ROW + 2
    ws.cell(row=cat_header_row, column=1, value="Fee category (reference)")
    _style_header_row(ws, cat_header_row, 1, theme)
    for i, category in enumerate(CATEGORIES, start=cat_header_row + 1):
        ws.cell(row=i, column=1, value=category.value)

    theme_header_row = cat_header_row + len(CATEGORIES) + 2
    ws.cell(row=theme_header_row, column=1, value="Theme colors (reference — edit theme.json to change)")
    _style_header_row(ws, theme_header_row, 2, theme)
    for i, (name, hexval) in enumerate(theme.colors.items(), start=theme_header_row + 1):
        ws.cell(row=i, column=1, value=name)
        cell = ws.cell(row=i, column=2, value=f"#{hexval}")
        cell.fill = PatternFill("solid", fgColor=hexval)

    _autosize(ws, {"A": 32, "B": 22, "C": 22})
    _protect_sheet(ws)


# ---------------------------------------------------------------------------
# RawData sheet
# ---------------------------------------------------------------------------

RAWDATA_COLUMNS = [
    "mis_id",
    "bp_id",
    "bp_name",
    "container_id",
    "container_type",
    "category",
    "period",
    "aum",
    "loan",
    "aum_calc",
    "fee_amount",
    "fee_bps",
    "bracket",
    "currency",
    "is_estimated",
    "data_quality_flag",
    "source_query",
    "lookup_key",
]


def _write_rawdata_sheet(ws, theme: Theme, peer_df: pd.DataFrame) -> None:
    ws.append(RAWDATA_COLUMNS)
    for row in peer_df[RAWDATA_COLUMNS].itertuples(index=False):
        ws.append(list(row))

    last_row = ws.max_row
    last_col_letter = get_column_letter(len(RAWDATA_COLUMNS))
    table_ref = f"A1:{last_col_letter}{last_row}"
    table = Table(displayName="RawData", ref=table_ref)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2", showRowStripes=True, showFirstColumn=False
    )
    ws.add_table(table)

    for col_idx, col_name in enumerate(RAWDATA_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 16 if "id" not in col_name else 12

    _protect_sheet(ws)


# ---------------------------------------------------------------------------
# Input sheet
# ---------------------------------------------------------------------------

INPUT_BP_ROW = 3
INPUT_NAME_ROW = 4
INPUT_CURRENT_AUM_ROW = 5
INPUT_ADDITIONAL_AUM_ROW = 6
INPUT_PROMISED_AUM_ROW = 7
INPUT_PROMISED_BRACKET_ROW = 8
INPUT_TABLE_HEADER_ROW = 10
INPUT_TABLE_FIRST_ROW = INPUT_TABLE_HEADER_ROW + 1
INPUT_TABLE_LAST_ROW = INPUT_TABLE_FIRST_ROW + len(CATEGORIES) - 1
INPUT_BP_LIST_COL = 12  # column L, hidden helper list for the dropdown


def _write_input_sheet(ws, theme: Theme, scenario: ScenarioResult, peer_df: pd.DataFrame) -> None:
    _title(ws, "A1", "Discount Request Input", theme)

    ws.cell(row=INPUT_BP_ROW, column=1, value="Client BP ID")
    bp_cell = ws.cell(row=INPUT_BP_ROW, column=2, value=scenario.bp_id or "")
    _highlight_input(bp_cell, theme)

    ws.cell(row=INPUT_NAME_ROW, column=1, value="Client name")
    ws.cell(
        row=INPUT_NAME_ROW,
        column=2,
        value='=IFERROR(INDEX(RawData[bp_name], MATCH($B$3, RawData[bp_id], 0)), "")',
    )

    ws.cell(row=INPUT_CURRENT_AUM_ROW, column=1, value="Current AUM, calc (from data)")
    ws.cell(
        row=INPUT_CURRENT_AUM_ROW,
        column=2,
        value='=IFERROR(INDEX(RawData[aum_calc], MATCH($B$3, RawData[bp_id], 0)), 0)',
    ).number_format = theme.number_formats["currency"]

    ws.cell(row=INPUT_ADDITIONAL_AUM_ROW, column=1, value="Additional AUM assumption (new money)")
    add_aum_cell = ws.cell(row=INPUT_ADDITIONAL_AUM_ROW, column=2, value=0)
    add_aum_cell.number_format = theme.number_formats["currency"]
    _highlight_input(add_aum_cell, theme)

    ws.cell(row=INPUT_PROMISED_AUM_ROW, column=1, value="Promised AUM, calc")
    ws.cell(
        row=INPUT_PROMISED_AUM_ROW, column=2, value=f"=B{INPUT_CURRENT_AUM_ROW}+B{INPUT_ADDITIONAL_AUM_ROW}"
    ).number_format = theme.number_formats["currency"]

    ws.cell(row=INPUT_PROMISED_BRACKET_ROW, column=1, value="Promised bracket")
    ws.cell(
        row=INPUT_PROMISED_BRACKET_ROW,
        column=2,
        value=f"=LOOKUP(B{INPUT_PROMISED_AUM_ROW}, Config!$B${CONFIG_BRACKET_FIRST_ROW}:$B${CONFIG_BRACKET_LAST_ROW}, "
        f"Config!$A${CONFIG_BRACKET_FIRST_ROW}:$A${CONFIG_BRACKET_LAST_ROW})",
    )

    headers = ["Fee category", "Current fee (bps)", "Promised fee (bps)", "Current amount", "Promised amount"]
    for col, header in enumerate(headers, start=1):
        ws.cell(row=INPUT_TABLE_HEADER_ROW, column=col, value=header)
    _style_header_row(ws, INPUT_TABLE_HEADER_ROW, len(headers), theme)

    for i, category in enumerate(CATEGORIES):
        row = INPUT_TABLE_FIRST_ROW + i
        ws.cell(row=row, column=1, value=category.value)
        ws.cell(
            row=row,
            column=2,
            value=f'=IFERROR(INDEX(RawData[fee_bps], MATCH($B$3&"|"&$A{row}, RawData[lookup_key], 0)), "")',
        ).number_format = theme.number_formats["bps"]

        promised = scenario.promised_by_category.get(category)
        promised_cell = ws.cell(row=row, column=3, value=promised.fee_bps if promised else None)
        promised_cell.number_format = theme.number_formats["bps"]
        _highlight_input(promised_cell, theme)

        ws.cell(
            row=row, column=4, value=f'=IF(B{row}="","",B{row}/10000*$B${INPUT_CURRENT_AUM_ROW})'
        ).number_format = theme.number_formats["currency"]
        ws.cell(
            row=row, column=5, value=f'=IF(C{row}="","",C{row}/10000*$B${INPUT_PROMISED_AUM_ROW})'
        ).number_format = theme.number_formats["currency"]

    # Hidden helper column feeding the BP dropdown (data validation list
    # sources must point at a range, not an arbitrary formula).
    bp_ids = sorted(peer_df["bp_id"].unique())
    col_letter = get_column_letter(INPUT_BP_LIST_COL)
    ws.cell(row=1, column=INPUT_BP_LIST_COL, value="bp_id list (helper)")
    for i, bp_id in enumerate(bp_ids, start=2):
        ws.cell(row=i, column=INPUT_BP_LIST_COL, value=bp_id)
    ws.column_dimensions[col_letter].hidden = True

    dv = DataValidation(
        type="list",
        formula1=f"${col_letter}$2:${col_letter}${1 + len(bp_ids)}",
        allow_blank=True,
    )
    ws.add_data_validation(dv)
    dv.add(bp_cell)

    _autosize(ws, {"A": 34, "B": 16, "C": 16, "D": 16, "E": 16})
    _protect_sheet(ws)


# ---------------------------------------------------------------------------
# Stats sheet (hidden): peer stats table + chart point data + client
# position formulas that place the current/promised markers.
# ---------------------------------------------------------------------------

STATS_HEADER_ROW = 1
STATS_FIRST_ROW = 2
STATS_LAST_ROW = STATS_FIRST_ROW + len(CATEGORIES) * len(BRACKETS) - 1

# Per-category chart-point block layout: fixed row ranges, one column block
# per category (10 columns each), starting at column M.
CHART_BLOCK_START_COL = 13  # column M
CHART_BLOCK_WIDTH = 10
BOX_FIRST_ROW = 2
BOX_ROWS_PER_BRACKET = 6
BOX_LAST_ROW = BOX_FIRST_ROW + BOX_ROWS_PER_BRACKET * len(BRACKETS) - 1
MEDIAN_ROWS_PER_BRACKET = 3
MEDIAN_LAST_ROW = BOX_FIRST_ROW + MEDIAN_ROWS_PER_BRACKET * len(BRACKETS) - 1
CLIENT_ROW = 2  # single row holding current/promised marker formulas

# Shared client-position summary block (bracket depends on total AUM, not
# on category, so it only needs to be computed once).
CLIENT_SUMMARY_ROW0 = 40
CLIENT_SUMMARY = {
    "current_aum": CLIENT_SUMMARY_ROW0,
    "current_bracket": CLIENT_SUMMARY_ROW0 + 1,
    "current_bracket_index": CLIENT_SUMMARY_ROW0 + 2,
    "promised_aum": CLIENT_SUMMARY_ROW0 + 3,
    "promised_bracket": CLIENT_SUMMARY_ROW0 + 4,
    "promised_bracket_index": CLIENT_SUMMARY_ROW0 + 5,
}


def _category_block_col(category_idx: int) -> int:
    return CHART_BLOCK_START_COL + category_idx * CHART_BLOCK_WIDTH


def _write_stats_sheet(ws, theme: Theme, peer_df: pd.DataFrame) -> None:
    _write_peer_stats_table(ws, theme, peer_df)
    _write_client_summary_block(ws)
    for idx, category in enumerate(CATEGORIES):
        _write_category_chart_block(ws, peer_df, idx, category)


def _write_peer_stats_table(ws, theme: Theme, peer_df: pd.DataFrame) -> None:
    from pricing_logic import compute_peer_stats  # local import avoids a cycle at module load time

    headers = ["category", "bracket", "bracket_index", "n_mis", "min", "p10", "q1", "median", "q3", "p90", "max"]
    for col, header in enumerate(headers, start=1):
        ws.cell(row=STATS_HEADER_ROW, column=col, value=header)

    row = STATS_FIRST_ROW
    for category in CATEGORIES:
        for bracket_idx, bracket in enumerate(BRACKETS, start=1):
            stats = compute_peer_stats(peer_df, category, bracket.label, exclude_bp_id=None)
            values = [
                category.value,
                bracket.label,
                bracket_idx,
                stats.n_mis,
                stats.min,
                stats.p10,
                stats.q1,
                stats.median,
                stats.q3,
                stats.p90,
                stats.max,
            ]
            for col, value in enumerate(values, start=1):
                ws.cell(row=row, column=col, value=value)
            row += 1


def _write_client_summary_block(ws) -> None:
    ws.cell(row=CLIENT_SUMMARY["current_aum"], column=1, value="Current AUM calc")
    ws.cell(row=CLIENT_SUMMARY["current_aum"], column=2, value=f"=Input!$B${INPUT_CURRENT_AUM_ROW}")

    ws.cell(row=CLIENT_SUMMARY["current_bracket"], column=1, value="Current bracket")
    ws.cell(
        row=CLIENT_SUMMARY["current_bracket"],
        column=2,
        value=f"=LOOKUP(B{CLIENT_SUMMARY['current_aum']}, "
        f"Config!$B${CONFIG_BRACKET_FIRST_ROW}:$B${CONFIG_BRACKET_LAST_ROW}, "
        f"Config!$A${CONFIG_BRACKET_FIRST_ROW}:$A${CONFIG_BRACKET_LAST_ROW})",
    )

    ws.cell(row=CLIENT_SUMMARY["current_bracket_index"], column=1, value="Current bracket index")
    ws.cell(
        row=CLIENT_SUMMARY["current_bracket_index"],
        column=2,
        value=f"=MATCH(B{CLIENT_SUMMARY['current_bracket']}, "
        f"Config!$A${CONFIG_BRACKET_FIRST_ROW}:$A${CONFIG_BRACKET_LAST_ROW}, 0)",
    )

    ws.cell(row=CLIENT_SUMMARY["promised_aum"], column=1, value="Promised AUM calc")
    ws.cell(row=CLIENT_SUMMARY["promised_aum"], column=2, value=f"=Input!$B${INPUT_PROMISED_AUM_ROW}")

    ws.cell(row=CLIENT_SUMMARY["promised_bracket"], column=1, value="Promised bracket")
    ws.cell(
        row=CLIENT_SUMMARY["promised_bracket"],
        column=2,
        value=f"=LOOKUP(B{CLIENT_SUMMARY['promised_aum']}, "
        f"Config!$B${CONFIG_BRACKET_FIRST_ROW}:$B${CONFIG_BRACKET_LAST_ROW}, "
        f"Config!$A${CONFIG_BRACKET_FIRST_ROW}:$A${CONFIG_BRACKET_LAST_ROW})",
    )

    ws.cell(row=CLIENT_SUMMARY["promised_bracket_index"], column=1, value="Promised bracket index")
    ws.cell(
        row=CLIENT_SUMMARY["promised_bracket_index"],
        column=2,
        value=f"=MATCH(B{CLIENT_SUMMARY['promised_bracket']}, "
        f"Config!$A${CONFIG_BRACKET_FIRST_ROW}:$A${CONFIG_BRACKET_LAST_ROW}, 0)",
    )


def _write_category_chart_block(ws, peer_df: pd.DataFrame, category_idx: int, category: Category) -> None:
    from pricing_logic import compute_peer_stats

    base_col = _category_block_col(category_idx)
    box_x_col, box_y_col = base_col, base_col + 1
    wh_x_col, wh_y_col = base_col + 2, base_col + 3
    med_x_col, med_y_col = base_col + 4, base_col + 5
    cur_x_col, cur_y_col = base_col + 6, base_col + 7
    prom_x_col, prom_y_col = base_col + 8, base_col + 9

    row = BOX_FIRST_ROW
    for bracket_idx, bracket in enumerate(BRACKETS, start=1):
        stats = compute_peer_stats(peer_df, category, bracket.label, exclude_bp_id=None)
        for x, y in _box_points(bracket_idx, stats):
            ws.cell(row=row, column=box_x_col, value=x)
            ws.cell(row=row, column=box_y_col, value=y)
            row += 1
    assert row - 1 == BOX_LAST_ROW

    row = BOX_FIRST_ROW
    for bracket_idx, bracket in enumerate(BRACKETS, start=1):
        stats = compute_peer_stats(peer_df, category, bracket.label, exclude_bp_id=None)
        for x, y in _whisker_points(bracket_idx, stats):
            ws.cell(row=row, column=wh_x_col, value=x)
            ws.cell(row=row, column=wh_y_col, value=y)
            row += 1

    row = BOX_FIRST_ROW
    for bracket_idx, bracket in enumerate(BRACKETS, start=1):
        stats = compute_peer_stats(peer_df, category, bracket.label, exclude_bp_id=None)
        for x, y in _median_points(bracket_idx, stats):
            ws.cell(row=row, column=med_x_col, value=x)
            ws.cell(row=row, column=med_y_col, value=y)
            row += 1
    assert row - 1 == MEDIAN_LAST_ROW

    input_row = INPUT_TABLE_FIRST_ROW + CATEGORIES.index(category)
    ws.cell(row=CLIENT_ROW, column=cur_x_col, value=f"=B{CLIENT_SUMMARY['current_bracket_index']}")
    ws.cell(row=CLIENT_ROW, column=cur_y_col, value=f"=Input!$B${input_row}")
    ws.cell(row=CLIENT_ROW, column=prom_x_col, value=f"=B{CLIENT_SUMMARY['promised_bracket_index']}")
    ws.cell(row=CLIENT_ROW, column=prom_y_col, value=f"=Input!$C${input_row}")


def _box_points(bracket_idx: int, stats: PeerStats) -> list[tuple[object, object]]:
    if not stats.has_data:
        return [(NA, NA)] * BOX_ROWS_PER_BRACKET
    x = bracket_idx
    return [
        (x - HALF_WIDTH, stats.q1),
        (x + HALF_WIDTH, stats.q1),
        (x + HALF_WIDTH, stats.q3),
        (x - HALF_WIDTH, stats.q3),
        (x - HALF_WIDTH, stats.q1),
        (NA, NA),
    ]


def _whisker_points(bracket_idx: int, stats: PeerStats) -> list[tuple[object, object]]:
    if not stats.has_data:
        return [(NA, NA)] * BOX_ROWS_PER_BRACKET
    x = bracket_idx
    return [
        (x, stats.min),
        (x, stats.q1),
        (NA, NA),
        (x, stats.q3),
        (x, stats.max),
        (NA, NA),
    ]


def _median_points(bracket_idx: int, stats: PeerStats) -> list[tuple[object, object]]:
    if not stats.has_data:
        return [(NA, NA)] * MEDIAN_ROWS_PER_BRACKET
    x = bracket_idx
    return [(x - HALF_WIDTH, stats.median), (x + HALF_WIDTH, stats.median), (NA, NA)]


# ---------------------------------------------------------------------------
# Dashboard sheet
# ---------------------------------------------------------------------------

def _write_dashboard_sheet(ws, theme: Theme, scenario: ScenarioResult, ws_stats) -> None:
    _title(ws, "A1", "Fee Benchmark Dashboard", theme)
    ws["A2"] = f"Client BP ID: {scenario.bp_id or '(new prospect)'}"
    ws["A2"].font = theme.font(bold=True)

    kpi_labels = ["Current annual revenue", "Promised annual revenue", "Delta", "Delta %"]
    for col, label in enumerate(kpi_labels, start=1):
        cell = ws.cell(row=4, column=col, value=label)
        cell.fill = PatternFill("solid", fgColor=theme.colors["kpi_fill"])
        cell.font = theme.font(bold=True)

    ws.cell(row=5, column=1, value="=SUM(Input!D11:D15)").number_format = theme.number_formats["currency"]
    ws.cell(row=5, column=2, value="=SUM(Input!E11:E15)").number_format = theme.number_formats["currency"]
    ws.cell(row=5, column=3, value="=B5-A5").number_format = theme.number_formats["currency"]
    ws.cell(row=5, column=4, value='=IF(A5=0,"",C5/A5)').number_format = "0.0%"

    if scenario.warnings:
        ws.cell(row=7, column=1, value="Data-quality / estimation notes:")
        ws.cell(row=7, column=1).font = theme.font(bold=True, color=theme.colors["warning"])
        for i, warning in enumerate(scenario.warnings, start=8):
            ws.cell(row=i, column=1, value=f"- {warning}")
        chart_start_row = 8 + len(scenario.warnings) + 2
    else:
        chart_start_row = 9

    bracket_legend = ", ".join(f"{i + 1}={label}" for i, label in enumerate(BRACKET_LABELS))

    charts_per_row = 2
    chart_height_rows = 16
    chart_width_cols = 8
    for idx, category in enumerate(CATEGORIES):
        chart = _build_category_chart(ws_stats, idx, category, theme, bracket_legend)
        col = 1 + (idx % charts_per_row) * chart_width_cols
        row = chart_start_row + (idx // charts_per_row) * chart_height_rows
        anchor = f"{get_column_letter(col)}{row}"
        ws.add_chart(chart, anchor)

    _autosize(ws, {"A": 26, "B": 22, "C": 18, "D": 12})
    _protect_sheet(ws)


def _build_category_chart(ws_stats, category_idx: int, category: Category, theme: Theme, bracket_legend: str) -> ScatterChart:
    base_col = _category_block_col(category_idx)
    box_x_col, box_y_col = base_col, base_col + 1
    wh_x_col, wh_y_col = base_col + 2, base_col + 3
    med_x_col, med_y_col = base_col + 4, base_col + 5
    cur_x_col, cur_y_col = base_col + 6, base_col + 7
    prom_x_col, prom_y_col = base_col + 8, base_col + 9

    chart = ScatterChart()
    chart.title = category.value.replace("_", " ").title()
    chart.style = 2
    chart.height = 8
    chart.width = 15
    chart.x_axis.title = f"AUM Bracket ({bracket_legend})"
    chart.y_axis.title = "Fee (bps)"
    chart.x_axis.scaling.min = 0.5
    chart.x_axis.scaling.max = len(BRACKETS) + 0.5
    chart.x_axis.majorUnit = 1

    box_series = _line_series(ws_stats, box_x_col, box_y_col, BOX_FIRST_ROW, BOX_LAST_ROW, "Peer box (Q1-Q3)", theme.colors["box_outline"], width=20000)
    whisker_series = _line_series(ws_stats, wh_x_col, wh_y_col, BOX_FIRST_ROW, BOX_LAST_ROW, "Peer whiskers (min-max)", theme.colors["whisker_line"], width=12000)
    median_series = _line_series(ws_stats, med_x_col, med_y_col, BOX_FIRST_ROW, MEDIAN_LAST_ROW, "Peer median", theme.colors["median_line"], width=26000)
    current_series = _marker_series(ws_stats, cur_x_col, cur_y_col, CLIENT_ROW, "Current", theme.colors["marker_current"], "circle")
    promised_series = _marker_series(ws_stats, prom_x_col, prom_y_col, CLIENT_ROW, "Promised", theme.colors["marker_promised"], "diamond")

    for series in (box_series, whisker_series, median_series, current_series, promised_series):
        chart.series.append(series)

    return chart


def _line_series(ws, x_col: int, y_col: int, first_row: int, last_row: int, title: str, color: str, width: int) -> Series:
    xref = Reference(ws, min_col=x_col, min_row=first_row, max_row=last_row)
    yref = Reference(ws, min_col=y_col, min_row=first_row, max_row=last_row)
    series = Series(yref, xref, title=title)
    series.marker = Marker(symbol="none")
    series.graphicalProperties.line.solidFill = color
    series.graphicalProperties.line.width = width
    series.smooth = False
    return series


def _marker_series(ws, x_col: int, y_col: int, row: int, title: str, color: str, symbol: str) -> Series:
    xref = Reference(ws, min_col=x_col, min_row=row, max_row=row)
    yref = Reference(ws, min_col=y_col, min_row=row, max_row=row)
    series = Series(yref, xref, title=title)
    series.marker = Marker(symbol=symbol, size=9)
    series.marker.graphicalProperties.solidFill = color
    series.marker.graphicalProperties.line.noFill = True
    series.graphicalProperties.line.noFill = True
    return series
