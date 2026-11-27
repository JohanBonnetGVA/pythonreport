"""Model inputs: wide daily weather features + discharge target."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config, store

FEATURES = (
    "precipitation_sum", "rain_sum", "snowfall_sum", "snow_depth_last", "snow_depth_loss",
    "soil_moisture_0_to_7cm_mean", "soil_moisture_7_to_28cm_mean", "soil_moisture_28_to_100cm_mean",
    "temperature_2m_mean",
)


@dataclass
class Dataset:
    weather: pd.DataFrame  # columns MultiIndex (point, variable), daily index
    y: pd.Series           # daily discharge (m³/s)
    lat: float = 46.0

    def features(self) -> pd.DataFrame:
        """Flat feature table: <point>__<variable>."""
        w = self.weather.loc[:, (slice(None), list(FEATURES))]
        w.columns = [f"{p}__{v}" for p, v in w.columns]
        return w

    def basin(self) -> pd.DataFrame:
        """Basin-average forcing: rain, solid precip (mm), temperature per point."""
        w = self.weather
        rain = w.xs("rain_sum", axis=1, level=1)
        solid = (w.xs("precipitation_sum", axis=1, level=1) - rain).clip(lower=0)
        temp = w.xs("temperature_2m_mean", axis=1, level=1)
        return pd.concat({"rain": rain, "solid": solid, "temp": temp}, axis=1)


def oudin_pet(temp: pd.Series, lat_deg: float) -> pd.Series:
    """Oudin (2005) potential evapotranspiration (mm/day) from temperature only."""
    doy = temp.index.day_of_year.to_numpy()
    lat = np.deg2rad(lat_deg)
    decl = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(lat) * np.tan(decl), -1, 1))
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    re = 37.6 * dr * (ws * np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.sin(ws))  # MJ/m²/d
    pet = re / 2.45 * (temp.to_numpy() + 5) / 100
    return pd.Series(np.where(temp.to_numpy() + 5 > 0, pet, 0.0), index=temp.index)


def load(target: str = "discharge_max", station: str = config.TARGET_STATION) -> Dataset:
    """Load from the DuckDB store."""
    w = store.query("SELECT * FROM weather_daily ORDER BY time")
    w = w.pivot(index="time", columns="point").swaplevel(axis=1).sort_index(axis=1)
    q = store.query("SELECT time, %s AS q FROM discharge_daily WHERE station = $s ORDER BY time" % target,
                    s=station).set_index("time")["q"]
    return Dataset(weather=w, y=q.reindex(w.index))
