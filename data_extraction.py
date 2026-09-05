"""Data extraction layer.

Adapter to the existing `GETData('myquery.sqlfile') -> pd.DataFrame` utility,
plus a schema-matching fake data generator for local development and demos.

Only `get_raw_data`'s `mode == "real"` branch needs to change when this
module is copied into the work project — everything downstream
(`pricing_logic.py`, `excel_report.py`) consumes the canonical fact table
defined here and never needs to know whether the data is fake or real.
"""

from __future__ import annotations

import logging
from enum import Enum

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class ContainerType(str, Enum):
    """Account mandate type. MIS (relationship) -> BP (client) -> Container (account)."""

    DISCRETIONARY = "DISCRETIONARY"
    ADVISORY = "ADVISORY"
    EXECUTION_ONLY = "EXECUTION_ONLY"


class Category(str, Enum):
    """Fee category, per SPEC.md §5.1."""

    BROKERAGE = "BROKERAGE"
    BROKERAGE_OPTIONS_FX = "BROKERAGE_OPTIONS_FX"
    FIDUCIARY_DEPOSIT = "FIDUCIARY_DEPOSIT"
    ADVISORY = "ADVISORY"
    DISCRETIONARY = "DISCRETIONARY"


# Canonical fact table schema (SPEC.md §4.2). One row = one fee observation
# for one BP, one container, one category, one period. `load_client_universe`
# always returns exactly these columns, in this order.
FACT_TABLE_COLUMNS: list[str] = [
    "mis_id",
    "bp_id",
    "bp_name",
    "container_id",
    "container_type",
    "category",
    "period",
    "aum",
    "loan",
    "fee_amount",
    "currency",
    "is_estimated",
    "data_quality_flag",
    "source_query",
]

# Which logical query backs each category. In the work project these map to
# real `*.sqlfile` names passed to `GETData`.
QUERY_BY_CATEGORY: dict[Category, str] = {
    Category.BROKERAGE: "brokerage_fees.sql",
    Category.BROKERAGE_OPTIONS_FX: "brokerage_options_fx_estimate.sql",
    Category.FIDUCIARY_DEPOSIT: "fiduciary_deposit_fees.sql",
    Category.ADVISORY: "advisory_fees.sql",
    Category.DISCRETIONARY: "discretionary_fees.sql",
}


def get_raw_data(query_name: str, mode: str = "fake") -> pd.DataFrame:
    """Single adapter point to the database.

    In the work project, only the `mode == "real"` branch changes, e.g.:

        from db_utils import GETData
        return GETData(query_name)
    """
    if mode == "real":
        raise NotImplementedError(
            "Wire this up in the work project by calling the existing "
            "GETData(query_name) utility here."
        )
    if mode == "fake":
        return _fake_query_result(query_name)
    raise ValueError(f"Unknown mode: {mode!r}")


def load_client_universe(mode: str = "fake", seed: int = 42) -> pd.DataFrame:
    """Orchestrates the per-category queries into one normalized fact table.

    This is the only function `pricing_logic.py` should call.
    """
    if mode == "fake":
        df = generate_fake_relationship_data(seed=seed)
    else:
        frames = [get_raw_data(query, mode=mode) for query in QUERY_BY_CATEGORY.values()]
        df = pd.concat(frames, ignore_index=True)

    df = _validate_schema(df)
    logger.info(
        "Loaded %d fact rows across %d MIS, %d BPs",
        len(df),
        df["mis_id"].nunique(),
        df["bp_id"].nunique(),
    )
    return df


def _validate_schema(df: pd.DataFrame) -> pd.DataFrame:
    missing = set(FACT_TABLE_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Fact table is missing required columns: {sorted(missing)}")
    return df[FACT_TABLE_COLUMNS].reset_index(drop=True)


def _fake_query_result(query_name: str) -> pd.DataFrame:
    category = next((cat for cat, q in QUERY_BY_CATEGORY.items() if q == query_name), None)
    if category is None:
        raise ValueError(f"Unknown query: {query_name!r}")
    full = generate_fake_relationship_data()
    return full[full["category"] == category.value].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Fake data generator
# ---------------------------------------------------------------------------

# (bracket label, low, high, number of MIS relationships to seed in it) —
# purely for generating a realistic peer population; unrelated to the real
# bracket edges used downstream for classification (see pricing_logic.BRACKETS).
_BRACKET_SEED_TARGETS: list[tuple[str, float, float, int]] = [
    ("0-5M", 0, 5_000_000, 40),
    ("5-10M", 5_000_000, 10_000_000, 30),
    ("10-20M", 10_000_000, 20_000_000, 20),
    ("20M+", 20_000_000, 60_000_000, 15),
]

# Illustrative starting fee levels per category (bps), before AUM-based
# compression and noise are applied. Tune freely — this only feeds the fake
# peer population, not real figures.
_BASE_FEE_BPS: dict[Category, float] = {
    Category.BROKERAGE: 45.0,
    Category.BROKERAGE_OPTIONS_FX: 15.0,
    Category.FIDUCIARY_DEPOSIT: 10.0,
    Category.ADVISORY: 60.0,
    Category.DISCRETIONARY: 80.0,
}

_CONTAINER_TYPE_BY_CATEGORY: dict[Category, ContainerType] = {
    Category.BROKERAGE: ContainerType.EXECUTION_ONLY,
    Category.BROKERAGE_OPTIONS_FX: ContainerType.EXECUTION_ONLY,
    Category.FIDUCIARY_DEPOSIT: ContainerType.ADVISORY,
    Category.ADVISORY: ContainerType.ADVISORY,
    Category.DISCRETIONARY: ContainerType.DISCRETIONARY,
}


def generate_fake_relationship_data(seed: int = 42, period: str = "2026-08") -> pd.DataFrame:
    """Schema-matching fake data (SPEC.md §10).

    Several MIS, a handful of BPs per MIS, one container per BP per
    category, with fee bps compressing as AUM grows (typical fee-schedule
    behavior) plus noise. Deliberately includes flagged FIDUCIARY_DEPOSIT
    rows and estimated BROKERAGE_OPTIONS_FX rows so downstream handling of
    both is exercised.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    bp_counter = 0
    mis_counter = 0

    for _label, low, high, n_mis in _BRACKET_SEED_TARGETS:
        for _ in range(n_mis):
            mis_counter += 1
            mis_id = f"MIS{mis_counter:04d}"
            n_bp = int(rng.integers(1, 4))
            for _ in range(n_bp):
                bp_counter += 1
                bp_id = f"BP{bp_counter:05d}"
                aum = float(rng.uniform(low, high))
                loan = float(min(0.0, -rng.uniform(0, aum * 0.3)) if rng.random() < 0.25 else 0.0)
                aum_calc = aum + abs(loan)

                for category in Category:
                    if rng.random() < 0.15:
                        continue  # not every BP has every fee category

                    base_bps = _BASE_FEE_BPS[category]
                    compression = max(0.4, 1 - aum_calc / 80_000_000)
                    noise = rng.normal(1.0, 0.15)
                    fee_bps = max(1.0, base_bps * compression * noise)
                    fee_amount = fee_bps / 10_000 * aum_calc

                    is_estimated = category == Category.BROKERAGE_OPTIONS_FX
                    data_quality_flag = (
                        "fiduciary deposit: source incomplete, verify manually"
                        if category == Category.FIDUCIARY_DEPOSIT and rng.random() < 0.3
                        else None
                    )

                    rows.append(
                        {
                            "mis_id": mis_id,
                            "bp_id": bp_id,
                            "bp_name": f"Client {bp_id}",
                            "container_id": f"{bp_id}-{category.value[:3]}",
                            "container_type": _CONTAINER_TYPE_BY_CATEGORY[category].value,
                            "category": category.value,
                            "period": period,
                            "aum": round(aum, 2),
                            "loan": round(loan, 2),
                            "fee_amount": round(fee_amount, 2),
                            "currency": "CHF",
                            "is_estimated": is_estimated,
                            "data_quality_flag": data_quality_flag,
                            "source_query": QUERY_BY_CATEGORY[category],
                        }
                    )

    return pd.DataFrame(rows, columns=FACT_TABLE_COLUMNS)
