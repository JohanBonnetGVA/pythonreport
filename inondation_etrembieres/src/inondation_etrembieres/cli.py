"""CLI: inondation-fetch {weather,discharge}."""
import argparse
import logging

from . import config
from .sources import discharge, weather

log = logging.getLogger(__name__)


def main() -> None:
    p = argparse.ArgumentParser(prog="inondation-fetch")
    p.add_argument("source", choices=("weather", "discharge", "all"))
    p.add_argument("--start", type=lambda s: config.date.fromisoformat(s), default=config.START)
    p.add_argument("--end", type=lambda s: config.date.fromisoformat(s), default=config.END)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if a.source in ("weather", "all"):
        for pt in config.POINTS:
            log.info("weather %s", pt.name)
            weather.fetch_point(pt, a.start, a.end)
    if a.source in ("discharge", "all"):
        log.info("discharge %s", config.DISCHARGE_STATION)
        discharge.load_station()


if __name__ == "__main__":
    main()
