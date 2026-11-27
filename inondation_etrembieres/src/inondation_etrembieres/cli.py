"""CLI: inondation-fetch {weather,discharge}."""
import argparse
import logging

from . import config, store
from .sources import discharge, hubeau, weather

log = logging.getLogger(__name__)


def main() -> None:
    p = argparse.ArgumentParser(prog="inondation-fetch")
    p.add_argument("source", choices=("weather", "discharge", "hubeau", "db", "all", "models", "gefs", "hindcast", "conditions", "alerts"))
    p.add_argument("--start", type=lambda s: config.date.fromisoformat(s), default=config.START)
    p.add_argument("--end", type=lambda s: config.date.fromisoformat(s), default=config.END)
    p.add_argument("--temp", choices=("gefs", "era5"), default="gefs", help="hindcast: forecast or observed temperature")
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
        print(run(temp=a.temp).round(2).to_string(index=False))
    if a.source == "conditions":
        from .hindcast import run_conditions
        print(run_conditions().round(2).to_string(index=False))
    if a.source == "alerts":
        import pandas as pd
        from . import alerts
        from .dataset import load
        from .hindcast import run, run_conditions
        h = alerts.hard(load())
        h.to_csv(config.DATA_PROCESSED / "alert_hard.csv")
        print("HARD alert, events and last 30 days:")
        for a_, b_ in (("2015-04-28", "2015-05-04"), ("2023-11-10", "2023-11-16")):
            print(h.loc[a_:b_, ["color", "condition_level", "q_mean", "rise", "rain_3d_sigma", "rain_on_snow"]].round(2).to_string())
        print(h.dropna(subset=["q_mean"]).iloc[-30:][["color", "condition_level", "q_mean", "rain_3d_sigma", "snow_m"]].round(2).to_string())
        f = alerts.forecast(run_conditions(), run())
        f.to_csv(config.DATA_PROCESSED / "alert_forecast.csv", index=False)
        print("FORECAST alert (GEFS hindcast):")
        print(f.round(2).to_string(index=False))
    if a.source == "models":
        from .dataset import load
        from .manager import ModelManager
        mm = ModelManager(load())
        mm.run()
        mm.save()
        print(mm.compare().round(2).T.to_string())


if __name__ == "__main__":
    main()
