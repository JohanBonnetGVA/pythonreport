"""Open-Meteo archive, model era5_seamless: ERA5-Land (~9 km) for snow, soil, temperature;
ERA5 (~25 km) fills precipitation/rain/snowfall, which ERA5-Land does not serve on Open-Meteo."""
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
MODEL = "era5_seamless"
CHUNK_YEARS = 2  # smaller requests avoid read timeouts
RATE_LIMIT_WAIT_S = 120   # free tier: 600/min, 5 000/h, 10 000/day weighted calls
RATE_LIMIT_MAX_WAIT_S = 3900
log = logging.getLogger(__name__)


class RateLimited(RuntimeError):
    """Open-Meteo quota exhausted; re-run later, finished chunks are kept."""


def _get(s, params: dict) -> dict:
    """GET; on 429 wait for the hourly quota, stop at once on the daily quota."""
    waited = 0
    while True:
        r = s.get(URL, timeout=180, params=params)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
        msg = r.text[:200]
        if "daily" in msg.lower() or waited >= RATE_LIMIT_MAX_WAIT_S:
            raise RateLimited(msg)
        log.warning("429 rate limit (%s), waiting %ss", msg, RATE_LIMIT_WAIT_S)
        time.sleep(RATE_LIMIT_WAIT_S)
        waited += RATE_LIMIT_WAIT_S


def _chunks(start: date, end: date):
    for y in range(start.year, end.year + 1, CHUNK_YEARS):
        yield max(start, date(y, 1, 1)), min(end, date(y + CHUNK_YEARS - 1, 12, 31))


def fetch_hourly(point: Point, start: date, end: date, s=None) -> pd.DataFrame:
    """Hourly ERA5-Land series for one point and one period."""
    s = s or session()
    df = pd.DataFrame(_get(s, {
        "latitude": point.lat, "longitude": point.lon, "elevation": point.elevation,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY), "models": MODEL, "timezone": TIMEZONE,
    })["hourly"])
    empty = [c for c in HOURLY if c not in df or df[c].isna().all()]
    if empty:  # fail loudly: an all-null variable would silently become 0 after resampling
        raise ValueError(f"{point.name} {start}→{end}: no data for {empty} (model {MODEL})")
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")


def to_daily(h: pd.DataFrame) -> pd.DataFrame:
    """Daily aggregates + snowmelt proxy (daily snow-depth loss)."""
    r = h.resample("D")
    sums = r[["precipitation", "rain", "snowfall"]].sum(min_count=1)  # NaN stays NaN, not 0
    d = r.agg({
        "temperature_2m": ["mean", "min", "max"],
        "snow_depth": "last",
        "soil_moisture_0_to_7cm": "mean", "soil_moisture_7_to_28cm": "mean",
        "soil_moisture_28_to_100cm": "mean",
    })
    d.columns = [f"{c}_{f}" for c, f in d.columns]
    d = d.join(sums.add_suffix("_sum"))
    return _snow_loss(d)


def _snow_loss(d: pd.DataFrame) -> pd.DataFrame:
    d["snow_depth_loss"] = (-d["snow_depth_last"].diff()).clip(lower=0)
    return d


def fetch_point(point: Point, start: date, end: date) -> pd.DataFrame:
    """Fetch one point chunk by chunk, then merge to data/raw/weather/<name>.parquet.

    Each 2-year chunk is cached as daily data in data/raw/weather/chunks/, so an
    interrupted run resumes where it stopped. Chunks ending in the last 10 days
    are always re-fetched (ERA5-Land lags ~5 days and recent days are provisional).
    """
    cache = DATA_RAW / "weather" / "chunks"
    cache.mkdir(parents=True, exist_ok=True)
    fresh_after = end - timedelta(days=10)
    s, parts = session(), []
    for a, b in _chunks(start, end):
        f = cache / f"{point.name}_{a.year}.parquet"  # stable name, even for the growing last chunk
        if f.exists() and b < fresh_after:
            parts.append(pd.read_parquet(f))
            continue
        log.info("  %s %s → %s", point.name, a, b)
        d = to_daily(fetch_hourly(point, a, b, s))
        d.to_parquet(f)
        parts.append(d)
    daily = _snow_loss(pd.concat(parts).sort_index())
    daily.to_parquet(DATA_RAW / "weather" / f"{point.name}.parquet")
    return daily
