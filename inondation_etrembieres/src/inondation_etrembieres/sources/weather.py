"""Open-Meteo ERA5-Land archive: precipitation, snow, soil moisture, temperature."""
from datetime import date

import pandas as pd

from ..config import DATA_RAW, TIMEZONE, Point
from ._http import session

URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = (
    "temperature_2m", "precipitation", "rain", "snowfall", "snow_depth",
    "soil_moisture_0_to_7cm", "soil_moisture_7_to_28cm", "soil_moisture_28_to_100cm",
)
CHUNK_YEARS = 5


def fetch_hourly(point: Point, start: date, end: date) -> pd.DataFrame:
    """Hourly ERA5-Land series for one point, fetched in 5-year chunks."""
    s, frames = session(), []
    for y in range(start.year, end.year + 1, CHUNK_YEARS):
        a = max(start, date(y, 1, 1))
        b = min(end, date(y + CHUNK_YEARS - 1, 12, 31))
        r = s.get(URL, timeout=120, params={
            "latitude": point.lat, "longitude": point.lon, "elevation": point.elevation,
            "start_date": a.isoformat(), "end_date": b.isoformat(),
            "hourly": ",".join(HOURLY), "models": "era5_land", "timezone": TIMEZONE,
        })
        r.raise_for_status()
        frames.append(pd.DataFrame(r.json()["hourly"]))
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
    """Fetch, aggregate and cache one point to data/raw/weather/<name>.parquet."""
    daily = to_daily(fetch_hourly(point, start, end))
    out = DATA_RAW / "weather" / f"{point.name}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(out)
    return daily
