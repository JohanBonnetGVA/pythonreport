"""CLI: inondation-fetch {weather,discharge}."""
import argparse
import logging

from . import config, store
from .sources import discharge, hubeau, weather

log = logging.getLogger(__name__)


def main() -> None:
    p = argparse.ArgumentParser(prog="inondation-fetch")
    p.add_argument("source", choices=("weather", "discharge", "hubeau", "db", "all", "models", "gefs", "hindcast"))
    p.add_argument("--start", type=lambda s: config.date.fromisoformat(s), default=config.START)
    p.add_argument("--end", type=lambda s: config.date.fromisoformat(s), default=config.END)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if a.source in ("weather", "all"):
        for pt in config.POINTS:
            log.info("weather %s", pt.name)
            try:
                weather.fetch_point(pt, a.start, a.end)
            except weather.RateLimited as e:
                log.error("Open-Meteo quota reached (%s); re-run later to resume.", e)
                raise SystemExit(2)
    if a.source in ("hubeau", "all"):
        log.info("hubeau %s", hubeau.SITE)
        hubeau.fetch(a.start, a.end)
    if a.source in ("discharge", "all"):
        log.info("discharge %s", config.DISCHARGE_STATION)
        discharge.load_station()
    if a.source in ("db", "all"):
        log.info("build %s", store.DB_PATH)
        store.build()
    if a.source == "gefs":
        from .hindcast import EVENTS
        from .sources import gefs
        for peak in EVENTS.values():
            for d in gefs.init_dates(peak):
                gefs.fetch(d)
    if a.source == "hindcast":
        from .hindcast import run
        print(run().round(2).to_string(index=False))
    if a.source == "models":
        from .dataset import load
        from .manager import ModelManager
        mm = ModelManager(load())
        mm.run()
        mm.save()
        print(mm.compare().round(2).T.to_string())


if __name__ == "__main__":
    main()
