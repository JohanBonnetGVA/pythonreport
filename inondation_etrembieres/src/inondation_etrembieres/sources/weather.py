"""Open-Meteo ERA5-Land archive: precipitation, snow, soil moisture, temperature."""
import logging
import time
from datetime import date, timedelta

import pandas as pd

from ..config import DATA_RAW, TIMEZONE, Point
from ._http import session

URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = (
    "temperature_2m", "precipitation", "rain", "snowfall", "snow_depth",
    "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm", "soil_moisture_28_to_100cm",
)
CHUNK_YEARS = 5
RATE_LIMIT_WAIT_S = 120   # free tier: 600/min, 5 000/h, 10 000/day weighted calls
RATE_LIMIT_MAX_WAIT_S = 3900
log = logging.getLogger(__name__)


def _get(s, params: dict) -> dict:
    """GET with explicit wait on 429 (hourly quota resets within the hour)."""
    waited = 0
    while True:
        r = s.get(URL, timeout=180, params=params)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
        if waited >= RATE_LIMIT_MAX_WAIT_S:
            r.raise_for_status()
        log.warning("429 rate limit (%s), waiting %ss", r.text[:120], RATE_LIMIT_WAIT_S)
        time.sleep(RATE_LIMIT_WAIT_S)
        waited += RATE_LIMIT_WAIT_S


def fetch_hourly(point: Point, start: date, end: date) -> pd.DataFrame:
    """Hourly ERA5-Land series for one point, fetched in 5-year chunks."""
    s, frames = session(), []
    for y in range(start.year, end.year + 1, CHUNK_YEARS):
        a = max(start, date(y, 1, 1))
        b = min(end, date(y + CHUNK_YEARS - 1, 12, 31))
        log.info("  %s %s → %s", point.name, a, b)
        frames.append(pd.DataFrame(_get(s, {
            "latitude": point.lat, "longitude": point.lon, "elevation": point.elevation,
            "start_date": a.isoformat(), "end_date": b.isoformat(),
            "hourly": ",".join(HOURLY), "models": "era5_land", "timezone": TIMEZONE,
        })["hourly"]))
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


def to_daily(h: pd.DataFrame) -> pd.DataFrame:
    """Daily aggregates + snowmelt proxy (daily snow-depth loss)."""
    d = h.resample("D").agg({
        "temperature_2m": ["mean", "min", "max"],
        "precipitation": "sum", "rain": "sum", "snowfall": "sum",
        "snow_depth": "last",
        "soil_moisture_0_to_7cm": "mean", "soil_moisture_7_to_28cm": "mean",
        "soil_moisture_28_to_100cm": "mean",
    })
    d.columns = [f"{c}_{f}" for c, f in d.columns]
    d["snow_depth_loss"] = (-d["snow_depth_last"].diff()).clip(lower=0)
    return d


def fetch_point(point: Point, start: date, end: date) -> pd.DataFrame:
    """Fetch, aggregate and cache one point to data/raw/weather/<name>.parquet.

    Incremental: if the file exists, only the missing recent days are fetched
    (ERA5-Land lags ~5 days behind real time).
    """
    out = DATA_RAW / "weather" / f"{point.name}.parquet"
    old = pd.read_parquet(out) if out.exists() else None
    if old is not None and old.index.min().date() <= start:
        start = old.index.max().date() - timedelta(days=10)  # re-fetch tail: last days may be provisional
        if start >= end:
            return old
    daily = to_daily(fetch_hourly(point, start, end))
    if old is not None:
        daily = pd.concat([old[old.index < daily.index.min()], daily])
    out.parent.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(out)
    return daily
