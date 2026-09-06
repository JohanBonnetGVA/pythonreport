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
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
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


def _set_print_layout(ws) -> None:
    """Fit the sheet to one page wide (tall pages are fine) so it prints
    cleanly instead of being cut off mid-column."""
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = 0.3
    ws.page_margins.right = 0.3
    ws.page_margins.top = 0.4
    ws.page_margins.bottom = 0.4


def _thin_border(theme: Theme) -> Border:
    side = Side(style="thin", color=theme.colors["grid"])
    return Border(left=side, right=side, top=side, bottom=side)


def _section_header(ws, row: int, text: str, n_cols: int, theme: Theme) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=n_cols)
    cell = ws.cell(row=row, column=1, value=text)
    cell.font = theme.font(size_key="size_header", bold=True, color=theme.colors["primary"])
    cell.fill = PatternFill("solid", fgColor=theme.colors["kpi_fill"])
    cell.alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[row].height = 20


def _delta_conditional_format(ws, cell_range: str, theme: Theme) -> None:
    green = PatternFill("solid", fgColor="C6EFCE")
    green_font = Font(color="006100")
    red = PatternFill("solid", fgColor="FFC7CE")
    red_font = Font(color="9C0006")
    ws.conditional_formatting.add(
        cell_range, CellIsRule(operator="greaterThan", formula=["0"], fill=green, font=green_font)
    )
    ws.conditional_formatting.add(
        cell_range, CellIsRule(operator="lessThan", formula=["0"], fill=red, font=red_font)
    )


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
INPUT_GROWTH_RATE_ROW = 9
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

    ws.cell(row=INPUT_GROWTH_RATE_ROW, column=1, value="Assumed annual AUM growth (forecast)")
    growth_cell = ws.cell(row=INPUT_GROWTH_RATE_ROW, column=2, value=0.05)
    growth_cell.number_format = "0.0%"
    _highlight_input(growth_cell, theme)

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
    _set_print_layout(ws)


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

DASHBOARD_FEE_TABLE_COLS = [
    "Fee category",
    "Current (bps)",
    "Request (bps)",
    "Current amount",
    "Promised amount",
    "Delta amount",
    "Delta %",
    "Forecast Y1 amount",
]


def _write_dashboard_sheet(ws, theme: Theme, scenario: ScenarioResult, ws_stats) -> None:
    ws.sheet_view.showGridLines = False
    border = _thin_border(theme)
    n_cols = len(DASHBOARD_FEE_TABLE_COLS)
    last_col = get_column_letter(n_cols)

    ws.merge_cells(f"A1:{last_col}1")
    _title(ws, "A1", "Fee Benchmark Dashboard", theme)
    ws.row_dimensions[1].height = 26

    ws.merge_cells(f"A2:{last_col}2")
    ws["A2"] = f"Client BP ID: {scenario.bp_id or '(new prospect)'}"
    ws["A2"].font = theme.font(bold=True, color=theme.colors["primary"])

    # --- KPI cards -----------------------------------------------------
    kpi_labels = ["Current annual revenue", "Promised annual revenue", "Delta", "Delta %"]
    for col, label in enumerate(kpi_labels, start=1):
        cell = ws.cell(row=4, column=col, value=label)
        cell.fill = PatternFill("solid", fgColor=theme.colors["kpi_fill"])
        cell.font = theme.font(bold=True, color=theme.colors["primary"])
        cell.border = border
        cell.alignment = Alignment(horizontal="center")

    kpi_current = ws.cell(
        row=5, column=1, value=f"=SUM(Input!D{INPUT_TABLE_FIRST_ROW}:D{INPUT_TABLE_LAST_ROW})"
    )
    kpi_promised = ws.cell(
        row=5, column=2, value=f"=SUM(Input!E{INPUT_TABLE_FIRST_ROW}:E{INPUT_TABLE_LAST_ROW})"
    )
    kpi_delta = ws.cell(row=5, column=3, value="=B5-A5")
    kpi_delta_pct = ws.cell(row=5, column=4, value='=IF(A5=0,"",C5/A5)')
    kpi_current.number_format = theme.number_formats["currency"]
    kpi_promised.number_format = theme.number_formats["currency"]
    kpi_delta.number_format = theme.number_formats["currency"]
    kpi_delta_pct.number_format = "0.0%"
    ws.row_dimensions[5].height = 22
    for col in range(1, 5):
        cell = ws.cell(row=5, column=col)
        cell.border = border
        cell.font = theme.font(size_key="size_header", bold=True)
        cell.alignment = Alignment(horizontal="center")
    _delta_conditional_format(ws, "C5:D5", theme)

    row = 7
    if scenario.warnings:
        ws.cell(row=row, column=1, value="Data-quality / estimation notes:")
        ws.cell(row=row, column=1).font = theme.font(bold=True, color=theme.colors["warning"])
        row += 1
        for warning in scenario.warnings:
            ws.cell(row=row, column=1, value=f"- {warning}")
            row += 1
        row += 1

    # --- Fee detail by category -----------------------------------------
    _section_header(ws, row, "Fee Detail by Category", n_cols, theme)
    row += 1

    table_header_row = row
    for col, header in enumerate(DASHBOARD_FEE_TABLE_COLS, start=1):
        cell = ws.cell(row=table_header_row, column=col, value=header)
        cell.border = border
    _style_header_row(ws, table_header_row, n_cols, theme)
    row += 1

    first_data_row = row
    for idx, category in enumerate(CATEGORIES):
        input_row = INPUT_TABLE_FIRST_ROW + idx
        values = [
            f"=Input!A{input_row}",
            f"=Input!B{input_row}",
            f"=Input!C{input_row}",
            f"=Input!D{input_row}",
            f"=Input!E{input_row}",
            f"=E{row}-D{row}",
            f'=IF(D{row}=0,"",F{row}/D{row})',
            f"=E{row}*(1+Input!$B${INPUT_GROWTH_RATE_ROW})",
        ]
        zebra = idx % 2 == 1
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = border
            if zebra:
                cell.fill = PatternFill("solid", fgColor="F2F2F2")
        ws.cell(row=row, column=2).number_format = theme.number_formats["bps"]
        ws.cell(row=row, column=3).number_format = theme.number_formats["bps"]
        ws.cell(row=row, column=4).number_format = theme.number_formats["currency"]
        ws.cell(row=row, column=5).number_format = theme.number_formats["currency"]
        ws.cell(row=row, column=6).number_format = theme.number_formats["currency"]
        ws.cell(row=row, column=7).number_format = "0.0%"
        ws.cell(row=row, column=8).number_format = theme.number_formats["currency"]
        row += 1
    last_data_row = row - 1

    total_row = row
    total_values = {
        1: "Total",
        2: f'=IF(Input!B{INPUT_CURRENT_AUM_ROW}=0,"",D{total_row}/Input!B{INPUT_CURRENT_AUM_ROW}*10000)',
        3: f'=IF(Input!B{INPUT_PROMISED_AUM_ROW}=0,"",E{total_row}/Input!B{INPUT_PROMISED_AUM_ROW}*10000)',
        4: f"=SUM(D{first_data_row}:D{last_data_row})",
        5: f"=SUM(E{first_data_row}:E{last_data_row})",
        6: f"=E{total_row}-D{total_row}",
        7: f'=IF(D{total_row}=0,"",F{total_row}/D{total_row})',
        8: f"=SUM(H{first_data_row}:H{last_data_row})",
    }
    for col, value in total_values.items():
        cell = ws.cell(row=total_row, column=col, value=value)
        cell.border = border
        cell.font = theme.font(bold=True)
        cell.fill = PatternFill("solid", fgColor=theme.colors["kpi_fill"])
    ws.cell(row=total_row, column=2).number_format = theme.number_formats["bps"]
    ws.cell(row=total_row, column=3).number_format = theme.number_formats["bps"]
    ws.cell(row=total_row, column=4).number_format = theme.number_formats["currency"]
    ws.cell(row=total_row, column=5).number_format = theme.number_formats["currency"]
    ws.cell(row=total_row, column=6).number_format = theme.number_formats["currency"]
    ws.cell(row=total_row, column=7).number_format = "0.0%"
    ws.cell(row=total_row, column=8).number_format = theme.number_formats["currency"]

    _delta_conditional_format(ws, f"F{first_data_row}:G{total_row}", theme)

    note_row = total_row + 1
    ws.merge_cells(f"A{note_row}:{last_col}{note_row}")
    ws.cell(
        row=note_row,
        column=1,
        value=f'=CONCATENATE("Forecast assumes ", TEXT(Input!$B${INPUT_GROWTH_RATE_ROW}, "0.0%"), '
        f'" annual AUM growth at the requested fee (see Input sheet).")',
    )
    ws.cell(row=note_row, column=1).font = theme.font(color=theme.colors["primary"])

    chart_start_row = note_row + 3

    # --- Peer benchmarking charts ----------------------------------------
    _section_header(ws, chart_start_row - 1, "Peer Benchmarking", n_cols, theme)

    bracket_legend = ", ".join(f"{i + 1}={label}" for i, label in enumerate(BRACKET_LABELS))

    charts_per_row = 2
    chart_height_rows = 16
    chart_width_cols = 8
    for idx, category in enumerate(CATEGORIES):
        chart = _build_category_chart(ws_stats, idx, category, theme, bracket_legend)
        col = 1 + (idx % charts_per_row) * chart_width_cols
        chart_row = chart_start_row + (idx // charts_per_row) * chart_height_rows
        anchor = f"{get_column_letter(col)}{chart_row}"
        ws.add_chart(chart, anchor)

    _autosize(ws, {"A": 26, "B": 16, "C": 16, "D": 18, "E": 18, "F": 16, "G": 12, "H": 18})
    _protect_sheet(ws)
    _set_print_layout(ws)


def _build_category_chart(ws_stats, category_idx: int, category: Category, theme: Theme, bracket_legend: str) -> ScatterChart:
    base_col = _category_block_col(category_idx)
    box_x_col, box_y_col = base_col, base_col + 1
    wh_x_col, wh_y_col = base_col + 2, base_col + 3
    med_x_col, med_y_col = base_col + 4, base_col + 5
    cur_x_col, cur_y_col = base_col + 6, base_col + 7
    prom_x_col, prom_y_col = base_col + 8, base_col + 9

    chart = ScatterChart()
    chart.scatterStyle = "lineMarker"
    chart.title = category.value.replace("_", " ").title()
    chart.title.overlay = False
    chart.style = 2
    chart.height = 8
    chart.width = 17
    chart.x_axis.title = f"AUM Bracket ({bracket_legend})"
    chart.x_axis.title.overlay = False
    chart.x_axis.title.tx.rich.bodyPr.rot = 0  # force horizontal, below the axis (not the vertical default)
    chart.y_axis.title = "Fee (bps)"
    chart.x_axis.scaling.min = 0.5
    chart.x_axis.scaling.max = len(BRACKETS) + 0.5
    chart.x_axis.majorUnit = 1
    chart.x_axis.majorTickMark = "out"
    chart.y_axis.majorTickMark = "out"
    chart.x_axis.axPos = "b"
    chart.y_axis.axPos = "l"
    chart.y_axis.delete = False
    chart.y_axis.tickLblPos = "nextTo"
    chart.y_axis.numFmt = "#,##0"
    chart.legend = None

    box_series_list = _box_series_list(ws_stats, box_x_col, box_y_col, theme.colors["box_outline"])
    whisker_series_list = _whisker_series_list(ws_stats, wh_x_col, wh_y_col, theme.colors["whisker_line"])
    median_series_list = _median_series_list(ws_stats, med_x_col, med_y_col, theme.colors["median_line"])
    current_series = _marker_series(ws_stats, cur_x_col, cur_y_col, CLIENT_ROW, "Current", theme.colors["marker_current"], "circle")
    promised_series = _marker_series(ws_stats, prom_x_col, prom_y_col, CLIENT_ROW, "Promised", theme.colors["marker_promised"], "diamond")

    series_list = [*box_series_list, *whisker_series_list, *median_series_list, current_series, promised_series]
    for series in series_list:
        chart.series.append(series)

    return chart


def _box_series_list(ws, x_col: int, y_col: int, color: str) -> list[Series]:
    """One closed 5-point rectangle series per bracket instead of a single
    series spanning every bracket with `=NA()` gap rows between them — see
    `_whisker_series_list` for why that gap approach isn't reliable."""
    series_list = []
    for bracket_idx in range(len(BRACKETS)):
        base_row = BOX_FIRST_ROW + bracket_idx * BOX_ROWS_PER_BRACKET
        series_list.append(
            _line_series(ws, x_col, y_col, base_row, base_row + 4, "Peer box (Q1-Q3)", color, width=20000)
        )
    return series_list


def _median_series_list(ws, x_col: int, y_col: int, color: str) -> list[Series]:
    """One short 2-point series per bracket for the median tick, instead of
    a single series spanning every bracket with `=NA()` gap rows between
    them — see `_whisker_series_list` for why that gap approach isn't
    reliable."""
    series_list = []
    for bracket_idx in range(len(BRACKETS)):
        base_row = BOX_FIRST_ROW + bracket_idx * MEDIAN_ROWS_PER_BRACKET
        series_list.append(
            _line_series(ws, x_col, y_col, base_row, base_row + 1, "Peer median", color, width=26000)
        )
    return series_list


def _whisker_series_list(ws, x_col: int, y_col: int, color: str) -> list[Series]:
    """One short 2-point line series per whisker segment (lower: min-q1,
    upper: q3-max) per bracket, instead of a single series spanning every
    bracket with `=NA()` gap rows — Excel doesn't reliably break the line at
    those gaps, which drew one zig-zagging "saw" line across all brackets.
    """
    series_list = []
    for bracket_idx in range(len(BRACKETS)):
        base_row = BOX_FIRST_ROW + bracket_idx * BOX_ROWS_PER_BRACKET
        for offset in (0, 3):  # rows 0-1 = min-q1, rows 3-4 = q3-max
            series_list.append(
                _line_series(
                    ws,
                    x_col,
                    y_col,
                    base_row + offset,
                    base_row + offset + 1,
                    "Peer whiskers (min-max)",
                    color,
                    width=12000,
                    marker_symbol="circle",
                    marker_size=5,
                )
            )
    return series_list


def _line_series(
    ws,
    x_col: int,
    y_col: int,
    first_row: int,
    last_row: int,
    title: str,
    color: str,
    width: int,
    marker_symbol: str = "none",
    marker_size: int = 5,
) -> Series:
    xref = Reference(ws, min_col=x_col, min_row=first_row, max_row=last_row)
    yref = Reference(ws, min_col=y_col, min_row=first_row, max_row=last_row)
    series = Series(yref, xref, title=title)
    series.marker = Marker(symbol=marker_symbol)
    if marker_symbol != "none":
        series.marker.size = marker_size
        series.marker.graphicalProperties.solidFill = color
        series.marker.graphicalProperties.line.solidFill = color
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
