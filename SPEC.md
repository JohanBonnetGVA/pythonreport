# Client Fee Benchmarking Report — Implementation Spec

Status: draft for hand-off to implementer (human or LLM).
Owner context: price-control tool used by a pricing committee to evaluate
banker discount requests against peer clients and, where the client already
exists, against that client's own current fee level.

---

## 1. Purpose

A banker asks for a fee discount for a client (existing or prospective).
Before granting it, the committee wants to see, per fee category:

- Where the **promised** fee level would sit relative to peer clients in the
  same AUM bracket (whisker/box plot).
- Where the client **currently** sits, if they are already a client.
- The revenue impact (current vs. promised) in one summary view.

This is a decision-support artifact, not a system of record. Output is a
single `.xlsx` file per request.

## 2. Scope / non-goals

- **In scope**: turning an already-extracted dataframe into a scenario
  (current vs. promised vs. peers) and rendering it as Excel.
- **Out of scope**: the Oracle connection itself. Extraction happens via an
  existing utility, `GETData('myquery.sqlfile') -> pd.DataFrame`, that
  already exists in the work project. This repo only defines the *shape*
  of data it expects and ships a fake-data generator with the same shape,
  so the real `GETData` call is a one-line swap later.
- **Out of scope**: authentication, FX conversion (assume a single
  reporting currency already normalized upstream — see §12).

## 3. File layout (5 code files max)

| File | Responsibility |
|---|---|
| `data_extraction.py` | Adapter to `GETData`, fake-data generator, normalization into one canonical fact table |
| `pricing_logic.py` | AUM calc, bracket assignment, peer stats, current-vs-promised scenario building |
| `excel_report.py` | Workbook/sheet/chart construction (reads `theme.json`, no business logic) |
| `main.py` | CLI entry point: wires the three modules together |
| `requirements.txt` | Pinned pip dependencies (config asset, not code) |
| `theme.json` | Corporate colors/fonts/number formats (config asset, not code — see §7.1) |

Strict separation matters here specifically because only `data_extraction.py`
needs to change when this is dropped into the work project (swap the fake
generator call for the real `GETData` calls). `pricing_logic.py` and
`excel_report.py` should never need to know whether data is fake or real.

`theme.json` and `requirements.txt` are config/data assets, not source files —
the "5 files" limit is about the 4 Python modules; adding a plain-data config
file alongside them doesn't violate the spirit of "keep this simple."

## 4. Data model

### 4.1 Hierarchy

```
MIS (relationship) -> BP (client) -> Container (account)
```

`container_type` ∈ `{DISCRETIONARY, ADVISORY, EXECUTION_ONLY}`.

### 4.2 Canonical fact table (long format)

One row = one fee observation for one BP, one container, one fee category,
one period. `data_extraction.py` must normalize whatever `GETData` returns
into exactly this shape — this is the contract the rest of the code relies on.

| Column | Type | Description |
|---|---|---|
| `mis_id` | str | Relationship manager / MIS identifier |
| `bp_id` | str | Client identifier (business partner) |
| `bp_name` | str | Client display name (may be blank for anonymized peers) |
| `container_id` | str | Account identifier |
| `container_type` | enum | `DISCRETIONARY` / `ADVISORY` / `EXECUTION_ONLY` |
| `category` | enum | See §5.1 |
| `period` | str (`YYYY-MM`) | Reporting period the fee row applies to |
| `aum` | float | Assets under management for the container |
| `loan` | float | Loan/lombard balance (signed as stored; may be negative) |
| `aum_calc` | float | `aum + abs(loan)` — computed, not extracted (§5.2) |
| `fee_amount` | float | Amount transferred to the bank from the client container for this fee, for the period. Already resolved to the bank's actual income by the source query — no gross/net split needed here (§5.1). |
| `fee_bps` | float | `fee_amount / aum_calc × 10000` — computed |
| `currency` | str | Assumed constant across the dataset (see §12) |
| `is_estimated` | bool | True when the value is a modeled estimate, not a DB figure |
| `data_quality_flag` | str \| None | Free-text caveat, e.g. `"fiduciary deposit: source incomplete"` |
| `source_query` | str | Which `.sqlfile` / logical query produced this row (traceability) |

### 4.3 Bracket table (config, not hardcoded)

| Bracket label | Lower (incl.) | Upper (excl.) |
|---|---|---|
| `0-5M` | 0 | 5,000,000 |
| `5-10M` | 5,000,000 | 10,000,000 |
| `10-20M` | 10,000,000 | 20,000,000 |
| `20M+` | 20,000,000 | ∞ |

These edges must live in one place (a constant in `pricing_logic.py`, mirrored
onto the Excel `Config` sheet) — never inlined into chart/formula logic,
because the user has already flagged these boundaries as likely to change.

## 5. Business rules

### 5.1 Fee categories and their caveats

| Category | Reliability | Notes |
|---|---|---|
| `BROKERAGE` | Good | No retrocession to account for. `fee_amount` is simply the transfer to the bank from the client container, already resolved as such inside the source query — use it directly. |
| `FIDUCIARY_DEPOSIT` | Poor | Known issues sourcing the existing book. Always set `data_quality_flag` on these rows; surface a visible warning wherever this category is shown, don't let it blend silently into aggregates. |
| `ADVISORY` | Good | Recurring, reliably in the DB. |
| `DISCRETIONARY` | Good | Recurring, reliably in the DB. |
| `BROKERAGE_OPTIONS_FX` | Estimated only | Currently not reliably queryable. `data_extraction.py` should expose a clearly named estimation function (e.g. a flat bps assumption or a ratio off total brokerage) with `is_estimated=True`, so the estimate is swappable later without touching the rest of the pipeline. |

### 5.2 AUM calc

```
aum_calc = aum + abs(loan)
```

Applied uniformly, including on hypothetical/promised scenarios where the
banker enters an additional-AUM assumption.

### 5.3 Bracket assignment

Pure function of `aum_calc` against the bracket table in §4.3. Must be
re-evaluated for the *promised* scenario too — a promise that brings in new
money can move the client into a different bracket, which changes which
peer group it should be benchmarked against.

## 6. Comparison / scenario logic (`pricing_logic.py`)

```python
def compute_aum_calc(df: pd.DataFrame) -> pd.DataFrame: ...

def assign_bracket(aum_calc: float, bracket_edges: list[tuple[str, float, float]]) -> str: ...

def compute_peer_stats(
    df: pd.DataFrame,
    category: str,
    bracket: str,
    exclude_bp_id: str | None,
) -> PeerStats:
    """Returns min/p10/q1/median/q3/p90/max plus `n_mis` (count of distinct
    `mis_id` contributing to this category/bracket — the peer-count figure
    shown on the chart). Always excludes the subject client's own rows from
    its own peer set. No suppression threshold — the count is just displayed
    as-is."""

@dataclass
class ScenarioResult:
    bp_id: str | None                 # None => new prospect, no "current" position
    current_by_category: dict[str, CategoryPosition]
    promised_by_category: dict[str, CategoryPosition]
    peer_stats_by_category_bracket: dict[tuple[str, str], PeerStats]
    warnings: list[str]               # data-quality / estimated notices

def build_scenario(
    df: pd.DataFrame,
    bp_id: str | None,
    promise_inputs: dict[str, float],   # category -> promised fee_bps
    additional_aum: float = 0.0,
) -> ScenarioResult: ...
```

`warnings` should accumulate every caveat above (estimated categories, flagged
data quality, small peer samples) so `excel_report.py` can render them as a
visible footer/banner rather than the committee finding out by accident.

## 7. Excel workbook design

One workbook per discount request. Sheets:

| Sheet | Purpose | Editable by banker? |
|---|---|---|
| `Input` | BP/client selection (dropdown from raw data), promised fee input per category, optional additional-AUM assumption | Yes — only the input cells, highlighted |
| `Dashboard` | The visual: small-multiple whisker charts (one per category) + a KPI summary (current vs. promised revenue, delta) + warnings banner | No |
| `RawData` | The full peer fact table as a native Excel Table, for the committee's own slicing | No (structured for one-time manual `Insert > PivotTable`, see §9) |
| `Stats` (hidden) | Precomputed per category×bracket peer quartiles + helper formulas that place the chart markers | No |
| `Config` | Bracket edges, category list — editable so a bracket change is a cell edit, not a code change | Yes, deliberately |

Sheet protection: lock every sheet except `Input` (and the bracket cells on
`Config`). Note in code comments that this is accident-prevention, not
security — real confidentiality controls belong at the file-storage layer.

### 7.1 Format file (`theme.json`)

Colors, fonts, and number formats live in `theme.json`, not inline in
`excel_report.py` — this is the "format file" the corporate look is driven
from, so styling can be tweaked (rebrand, tone down colors, resize fonts)
without touching code:

```json
{
  "colors": {
    "primary": "1F4E79",
    "box_fill_light": "DDEBF7",
    "box_fill_mid": "9DC3E6",
    "marker_current": "404040",
    "marker_promised": "C00000",
    "warning": "FFC000",
    "grid": "D9D9D9"
  },
  "fonts": { "family": "Calibri", "size_body": 10, "size_header": 12, "size_title": 14 },
  "number_formats": { "currency": "#,##0", "bps": "#,##0.0" }
}
```

`excel_report.py` loads this file once at startup and uses it everywhere
(cell styles, fills, chart series colors, fonts). It also writes a read-only
copy of these values onto the `Config` sheet purely for visibility — since
the workbook is regenerated from scratch on every run, editing that mirrored
copy in Excel wouldn't persist; `theme.json` is the actual source of truth.

Corporate-friendly styling checklist: single consistent font/size, a small
fixed color palette (2–3 accent colors + neutral greys, all from
`theme.json`), header banding, number formatting (thousands separator, bps
with 1 decimal), and a footer on `Dashboard` stamping generation date,
data-as-of period, and which BP/scenario it represents — expected in
anything that reaches a committee.

## 8. Chart design — whisker plot + moving marker

Native "Box & Whisker" charts are **not** supported by openpyxl chart
automation. Use the standard stacked-column approximation, built to look as
close to a real box plot as openpyxl allows:

**Box (per category, X axis = bracket label), `openpyxl.chart.BarChart`,
`type="col"`, `grouping="stacked"`, `overlap=100`:**
Segments, bottom to top:
1. `min` → no fill (spacer)
2. `q1 - min` → light theme fill
3. `median - q1` → mid theme fill
4. `q3 - median` → mid theme fill
5. `max - q3` → no fill (spacer)

**Whisker caps** — don't skip these, they're most of what makes it read as a
box plot rather than a bar chart. Use `openpyxl.chart.error_bar.ErrorBars`
on the min/max boundary points with `errBarType="minus"/"plus"` sourced from
helper cells on `Stats` (`min`→`q1` distance and `q3`→`max` distance), so the
cap sits exactly at min/max regardless of column height.

**Data labels**: show the median value (bps) on each box; show peer count
(`n_mis`) as a small label below the category axis tick.

**Client marker (current + promised), same chart** — `openpyxl.chart.ScatterChart`
combined onto the bar chart (`bar_chart += scatter_chart`):
- `x = bracket_index - 0.5` (centers the point over its column)
- `y = fee_bps`
- Give the scatter chart its own `x_axis`/`y_axis` with unique `axId`s, set
  the secondary x-axis `min=0`, `max=<bracket count>`, `delete=True` (hidden:
  no line, labels, or gridlines) so it aligns 1:1 with the primary category
  axis but is invisible. This axis-alignment trick is what lets a numeric XY
  series sit correctly on top of a category-axis bar chart in Excel.
- Two markers per chart: "Current" (neutral marker from `theme.json`,
  e.g. a circle) and "Promised" (accent marker, e.g. a diamond), joined by a
  thin connector line to show the direction of movement.
- Axis titles ("AUM Bracket" / "Fee, bps"), a light grey gridline on the
  value axis only, and a compact legend — all colors/fonts pulled from
  `theme.json`, never hardcoded in `excel_report.py`.

**Dynamic recompute in Excel:** promise inputs live on `Input`; bracket
lookup for the promised scenario is an Excel formula against the `Config`
bracket table (`LOOKUP`/`IFS`), not a Python-baked value — so if the banker
changes the promised AUM assumption, the bracket and the chart marker move
on their own when Excel recalculates. `Stats` holds the precomputed peer
quartiles (recomputing quantiles over the full peer set live in Excel is
unnecessary weight) but the classification of the promise into a bracket,
and the marker's plotted position, are formulas.

Flag this chart mechanic as the fiddliest part of the build — sanity-check
the first generated chart by hand against the `Stats` sheet numbers before
trusting the automated version.

### 8.1 Layout

`Dashboard` lays out one such chart per category as small multiples (grid,
e.g. 2×3), with the revenue KPI summary and warnings banner above them.

## 9. Library choice and known limitations

**Recommended library: `openpyxl`.** It's the most widely used and most
actively maintained Python/Excel library (it's pandas' own default `.xlsx`
engine), which matters for a file that's getting handed off and maintained
by someone else later — it's the library most likely to already be familiar,
documented, and supported long-term. It covers everything this project needs:
charts (bar, scatter, combo via secondary axes, error bars), Tables
(`ListObject`), data validation, conditional formatting, sheet protection,
and named ranges.

- **No native box-whisker chart** in openpyxl → use §8's stacked-column +
  error-bar approximation.
- **No Python-authored native PivotTable** — not needed here (decided out of
  scope); `RawData` ships as a plain Excel Table instead, which the committee
  can pivot manually with one click if they want to.
- Combo charts (bar + scatter on a shared/secondary axis, per §8) require
  manually assigning unique `axId`s and combining chart objects with
  `chart1 += chart2` — more manual than `xlsxwriter`'s combo API, but fully
  capable of the same result.

## 10. Fake data generator (`data_extraction.py`)

```python
def get_raw_data(query_name: str, mode: str = "fake") -> pd.DataFrame:
    """mode='fake' -> generate_fake_relationship_data(); mode='real' ->
    GETData(query_name). Only this branch changes in the work project."""

def generate_fake_relationship_data(seed: int = 42) -> pd.DataFrame:
    """Produces the canonical fact table from §4.2 directly — several MIS,
    tens of BPs per MIS, containers per BP, fee rows per category/period.
    Distribution notes:
      - aum lognormal, enough distinct MIS per bracket (>=15) for the peer
        stats to mean something, including the 20M+ bracket.
      - fee_bps should compress as aum_calc grows (typical fee-schedule
        behavior), with realistic noise.
      - include some FIDUCIARY_DEPOSIT rows with data_quality_flag set, and
        some BROKERAGE_OPTIONS_FX rows with is_estimated=True, so downstream
        handling of both is actually exercised."""

def load_client_universe(mode: str = "fake") -> pd.DataFrame:
    """Orchestrates per-category queries, concatenates, normalizes to §4.2."""
```

Keep the fake generator's output schema byte-for-byte identical to what the
real path must produce — that's what makes the later swap a one-line change
instead of a rewrite.

## 11. Engineering practices

- `requirements.txt`: pin exact versions (`pandas==...`, `openpyxl==...`).
  No conda, pip only, per existing convention.
- Never commit real client data; only the fake generator's output is fine in
  version control.
- Use `logging`, not `print` — this feeds a pricing decision, so log which
  query/mode ran, row counts, bracket assigned, and every warning raised.
  Treat it as an audit trail, not debug noise.
- `main.py` CLI surface: `--bp-id`, `--promise <category>=<bps>` (repeatable)
  or `--promise-file config.json`, `--additional-aum`, `--mode fake|real`,
  `--output path.xlsx`.

## 12. Open questions / assumptions to confirm before/while implementing

1. **Currency**: assumed single reporting currency, already normalized
   upstream of `GETData`. If the DB spans currencies, FX handling needs to be
   added to §4.2 — not addressed here.
2. **Whisker definition**: box = Q1/median/Q3, whiskers = true min/max by
   default; consider p10/p90 instead if single outliers turn out to distort
   the chart — pick one and make it a named constant, not a magic number.
3. **Client lookup on `Input`**: proposed as a dropdown (data validation)
   sourced from the `RawData` BP list, rather than free-text entry.
4. **RawData confidentiality**: it will contain other clients' fee levels for
   benchmarking — confirm this is acceptable for the committee audience (vs.
   needing anonymization of `bp_name`).

## 13. Summary of the main critique points

- Don't let the box-whisker library gap surprise the implementer mid-build —
  §8/§9 above are the workaround, not a bug to debug around.
- Keep bracket edges and the whisker definition as named constants/config,
  never inlined — brackets are explicitly called out as likely to change.
- Keep all styling (colors/fonts/number formats) in `theme.json`, never
  hardcoded in `excel_report.py` — that's what makes the corporate look
  maintainable without touching code.
- Never blend estimated (`BROKERAGE_OPTIONS_FX`) or flagged
  (`FIDUCIARY_DEPOSIT`) figures into a comparison without a visible caveat.
- Keep extraction/logic/presentation strictly separated so the work-project
  hand-off is a one-function swap, not a rewrite.
