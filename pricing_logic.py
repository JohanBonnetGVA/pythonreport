"""Business logic: AUM calc, bracket assignment, peer stats, and the
current-vs-promised scenario.

Pure functions and dataclasses over the canonical fact table produced by
`data_extraction.py` — no I/O, no Excel. This is what `excel_report.py`
consumes and what `main.py` wires together.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from data_extraction import Category

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Bracket:
    label: str
    low: float
    high: float  # exclusive; float("inf") for the open-ended top bracket

    def contains(self, aum_calc: float) -> bool:
        return self.low <= aum_calc < self.high


# AUM brackets (SPEC.md §4.3). Kept as one named constant, mirrored onto the
# Excel Config sheet, never inlined into formulas/branches elsewhere — these
# are explicitly expected to change.
BRACKETS: list[Bracket] = [
    Bracket("0-5M", 0, 5_000_000),
    Bracket("5-10M", 5_000_000, 10_000_000),
    Bracket("10-20M", 10_000_000, 20_000_000),
    Bracket("20M+", 20_000_000, float("inf")),
]


@dataclass
class PeerStats:
    """Peer distribution for one category/bracket. `n_mis` (distinct MIS
    count) is the peer-count figure shown on the chart, displayed as-is."""

    category: Category
    bracket: str
    n_mis: int
    min: float
    p10: float
    q1: float
    median: float
    q3: float
    p90: float
    max: float

    @property
    def has_data(self) -> bool:
        return self.n_mis > 0


@dataclass
class CategoryPosition:
    category: Category
    aum_calc: float
    bracket: str
    fee_amount: float
    fee_bps: float
    is_estimated: bool = False
    data_quality_flag: str | None = None


@dataclass
class ScenarioResult:
    bp_id: str | None  # None => new prospect, no "current" position
    current_by_category: dict[Category, CategoryPosition] = field(default_factory=dict)
    promised_by_category: dict[Category, CategoryPosition] = field(default_factory=dict)
    peer_stats: dict[tuple[Category, str], PeerStats] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def compute_aum_calc(df: pd.DataFrame) -> pd.DataFrame:
    """SPEC.md §5.2: aum_calc = aum + abs(loan)."""
    df = df.copy()
    df["aum_calc"] = df["aum"] + df["loan"].abs()
    return df


def assign_bracket(aum_calc: float, brackets: list[Bracket] = BRACKETS) -> str:
    for bracket in brackets:
        if bracket.contains(aum_calc):
            return bracket.label
    raise ValueError(f"aum_calc={aum_calc} did not match any bracket")


def annotate_fact_table(df: pd.DataFrame) -> pd.DataFrame:
    """Adds the computed columns the rest of the pipeline (and the Excel
    RawData sheet) rely on: aum_calc, fee_bps, bracket, lookup_key.

    `lookup_key` (`bp_id|category`) exists purely so Excel can do a
    single-criteria INDEX/MATCH instead of a two-criteria array formula.
    """
    df = compute_aum_calc(df)
    df["fee_bps"] = df["fee_amount"] / df["aum_calc"] * 10_000
    df["bracket"] = df["aum_calc"].apply(assign_bracket)
    df["lookup_key"] = df["bp_id"].astype(str) + "|" + df["category"].astype(str)
    return df


def compute_peer_stats(
    df: pd.DataFrame,
    category: Category,
    bracket: str,
    exclude_bp_id: str | None = None,
) -> PeerStats:
    """Peer distribution for one category/bracket, excluding the subject
    client's own rows so it never benchmarks against itself.
    """
    subset = df[(df["category"] == category.value) & (df["bracket"] == bracket)]
    if exclude_bp_id is not None:
        subset = subset[subset["bp_id"] != exclude_bp_id]

    if subset.empty:
        return PeerStats(category, bracket, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    fee_bps = subset["fee_bps"]
    return PeerStats(
        category=category,
        bracket=bracket,
        n_mis=int(subset["mis_id"].nunique()),
        min=float(fee_bps.min()),
        p10=float(fee_bps.quantile(0.10)),
        q1=float(fee_bps.quantile(0.25)),
        median=float(fee_bps.median()),
        q3=float(fee_bps.quantile(0.75)),
        p90=float(fee_bps.quantile(0.90)),
        max=float(fee_bps.max()),
    )


def build_scenario(
    df: pd.DataFrame,
    bp_id: str | None,
    promise_bps: dict[Category, float],
    additional_aum: float = 0.0,
) -> ScenarioResult:
    """Builds the current-vs-promised-vs-peers picture for one client (or a
    new prospect, if `bp_id` is None).

    `df` must already be annotated via `annotate_fact_table`.
    """
    scenario = ScenarioResult(bp_id=bp_id)
    client_rows = df[df["bp_id"] == bp_id] if bp_id else df.iloc[0:0]

    current_aum_calc = float(client_rows["aum_calc"].iloc[0]) if not client_rows.empty else 0.0
    promised_aum_calc = current_aum_calc + additional_aum
    promised_bracket = assign_bracket(promised_aum_calc)

    for category in Category:
        cat_rows = client_rows[client_rows["category"] == category.value]
        if not cat_rows.empty:
            row = cat_rows.iloc[0]
            scenario.current_by_category[category] = CategoryPosition(
                category=category,
                aum_calc=row["aum_calc"],
                bracket=row["bracket"],
                fee_amount=row["fee_amount"],
                fee_bps=row["fee_bps"],
                is_estimated=bool(row["is_estimated"]),
                data_quality_flag=row["data_quality_flag"],
            )
            if row["data_quality_flag"]:
                scenario.warnings.append(f"{category.value}: {row['data_quality_flag']}")
            if row["is_estimated"]:
                scenario.warnings.append(
                    f"{category.value}: current figure is estimated, not sourced from a query"
                )

        if category in promise_bps:
            fee_bps = promise_bps[category]
            fee_amount = fee_bps / 10_000 * promised_aum_calc
            scenario.promised_by_category[category] = CategoryPosition(
                category=category,
                aum_calc=promised_aum_calc,
                bracket=promised_bracket,
                fee_amount=fee_amount,
                fee_bps=fee_bps,
                is_estimated=(category == Category.BROKERAGE_OPTIONS_FX),
            )
            if category == Category.BROKERAGE_OPTIONS_FX:
                scenario.warnings.append(
                    f"{category.value}: promised figure relies on an estimation heuristic, not a query"
                )

        for bracket in BRACKETS:
            scenario.peer_stats[(category, bracket.label)] = compute_peer_stats(
                df, category, bracket.label, exclude_bp_id=bp_id
            )

    return scenario
