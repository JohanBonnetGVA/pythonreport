"""BAFU discharge, station 2170 (Arve – Genève, Bout du Monde).

Historical series are ordered from the BAFU hydrology data service
(hydrodaten.admin.ch) and dropped in data/raw/discharge/ as CSV.
Times are in winter time (UTC+1). Source must be credited: BAFU/FOEN.
"""
from pathlib import Path

import pandas as pd

from ..config import DATA_RAW, DISCHARGE_STATION


def load_csv(path: Path, time_col: str | None = None, value_col: str | None = None,
             sep: str | None = None) -> pd.Series:
    """Load a BAFU CSV export; guess the time and value columns if not given."""
    df = pd.read_csv(path, sep=sep, engine="python", comment="#")
    time_col = time_col or next(c for c in df.columns if "zeit" in c.lower() or "time" in c.lower() or "date" in c.lower())
    value_col = value_col or next(c for c in df.columns if c != time_col and pd.api.types.is_numeric_dtype(df[c]))
    s = pd.Series(df[value_col].to_numpy(), index=pd.to_datetime(df[time_col], dayfirst=True), name="discharge_m3s")
    return s.sort_index()


def daily_peak(s: pd.Series) -> pd.DataFrame:
    """Daily mean and peak discharge."""
    return s.resample("D").agg(["mean", "max"]).add_prefix("discharge_").rename_axis("time")


def load_station(station: str = DISCHARGE_STATION) -> pd.DataFrame:
    """Load every CSV for the station, merge, save to parquet."""
    files = sorted((DATA_RAW / "discharge").glob(f"*{station}*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV for station {station} in {DATA_RAW / 'discharge'}")
    s = pd.concat(load_csv(f) for f in files)
    s = s[~s.index.duplicated(keep="last")]
    d = daily_peak(s)
    d.to_parquet(DATA_RAW / "discharge" / f"{station}_daily.parquet")
    return d
