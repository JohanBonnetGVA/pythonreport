"""CLI entry point: load data, build the current-vs-promised scenario, and
render the Excel report.

Examples:
    python main.py --output report.xlsx
    python main.py --bp-id BP00001 --promise ADVISORY=55 --promise DISCRETIONARY=70 \\
        --additional-aum 2000000 --output report.xlsx
"""

from __future__ import annotations

import argparse
import logging

from data_extraction import Category, load_client_universe
from excel_report import build_workbook
from pricing_logic import annotate_fact_table, build_scenario

logger = logging.getLogger(__name__)


def _parse_promise(items: list[str]) -> dict[Category, float]:
    promise: dict[Category, float] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid --promise value {item!r}, expected CATEGORY=BPS")
        name, _, value = item.partition("=")
        try:
            category = Category(name.strip().upper())
        except ValueError:
            valid = ", ".join(c.value for c in Category)
            raise SystemExit(f"Unknown category {name!r} in --promise, expected one of: {valid}")
        try:
            promise[category] = float(value)
        except ValueError:
            raise SystemExit(f"Invalid bps value {value!r} in --promise {item!r}")
    return promise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a client fee benchmarking report.")
    parser.add_argument("--bp-id", default=None, help="Existing client BP id; omit for a new prospect")
    parser.add_argument(
        "--promise",
        action="append",
        default=[],
        metavar="CATEGORY=BPS",
        help="Promised fee in bps for a category, e.g. --promise ADVISORY=55. Repeatable.",
    )
    parser.add_argument(
        "--additional-aum", type=float, default=0.0, help="New-money assumption added to current AUM"
    )
    parser.add_argument("--mode", choices=["fake", "real"], default="fake")
    parser.add_argument("--seed", type=int, default=42, help="Fake data seed (mode=fake only)")
    parser.add_argument("--output", default="fee_benchmark_report.xlsx")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    promise_bps = _parse_promise(args.promise)

    df = load_client_universe(mode=args.mode, seed=args.seed)
    df = annotate_fact_table(df)
    scenario = build_scenario(df, bp_id=args.bp_id, promise_bps=promise_bps, additional_aum=args.additional_aum)

    build_workbook(scenario, df, output_path=args.output)
    logger.info("Report written to %s", args.output)


if __name__ == "__main__":
    main()
