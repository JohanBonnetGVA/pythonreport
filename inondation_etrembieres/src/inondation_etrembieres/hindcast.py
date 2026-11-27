"""Hindcast: what flood probability would GEFS + GR4J have given before 2015 and 2023?

For each 00Z init date (1–7 days before the peak) and each ensemble member:
  weather = ERA5 history up to init−1 (model state: snow, soil, stores) + GEFS days 1–7,
  run GR4J+snow (calibrated on 1990–2016), flood if Q ≥ the model's quantile-mapped threshold.
Probability = share of members flooding on any forecast day.
"""
import logging

import numpy as np
import pandas as pd

from . import config
from .dataset import Dataset, load
from .manager import ModelManager
from .peak import PeakFactor
from .sources import gefs

log = logging.getLogger(__name__)
EVENTS = {"2015": "2015-05-02", "2023": "2023-11-15"}  # observed daily-mean peaks at Reignier
LAPSE = 0.0065       # °C/m, GEFS grid elevation → point elevation
RAIN_SNOW = (0.0, 2.0)  # °C: all snow below, all rain above, linear in between


def scenario(hist: Dataset, fc: pd.DataFrame, member: str, init: pd.Timestamp, temp: str = "gefs") -> Dataset:
    """ERA5 up to init−1, then one GEFS member for days 1–7.

    temp="era5": replace the forecast temperature by the observed (ERA5) one — a
    "perfect temperature forecast" experiment isolating the temperature error.
    """
    w = hist.weather.loc[: init - pd.Timedelta(days=1)]
    f = fc[fc["member"] == member]
    elev = {p.name: p.elevation for p in config.POINTS}
    cols = {}
    for p, g in f.groupby("point"):
        g = g.set_index("date").sort_index()
        dz = elev[p] - g["z_grid"].fillna(elev[p])
        t = g["temp_c"] - LAPSE * dz
        liquid = ((t - RAIN_SNOW[0]) / (RAIN_SNOW[1] - RAIN_SNOW[0])).clip(0, 1)
        if temp == "era5":  # observed temperature + observed rain/snow phase (as in calibration)
            w_obs = hist.weather.reindex(g.index)
            t = w_obs[(p, "temperature_2m_mean")]
            frac = w_obs[(p, "rain_sum")] / w_obs[(p, "precipitation_sum")].where(lambda x: x > 0.1)
            liquid = frac.clip(0, 1).fillna(((t - RAIN_SNOW[0]) / (RAIN_SNOW[1] - RAIN_SNOW[0])).clip(0, 1))
        cols[(p, "precipitation_sum")] = g["precip_mm"]
        cols[(p, "rain_sum")] = g["precip_mm"] * liquid
        cols[(p, "temperature_2m_mean")] = t
    fw = pd.DataFrame(cols).reindex(columns=w.columns)
    weather = pd.concat([w, fw])
    return Dataset(weather=weather, y=hist.y.reindex(weather.index), lat=hist.lat)


def run(names=("gr4j_snow",), temp: str = "gefs") -> pd.DataFrame:
    data = load()
    mm = ModelManager(data, names=names)
    mm.run()
    thr_obs = config.flood_threshold(data.y)
    pf = PeakFactor().fit(data.y.loc[:"2022"], data.qmax.loc[:"2022"]) if data.qmax is not None else None
    rows = []
    for n, model in mm.models.items():
        thr = mm._relative_flood(n, thr_obs)["qmap threshold"]
        log.info("%s: model flood threshold %.0f m³/s (obs %.0f)", n, thr, thr_obs)
        for ev, peak in EVENTS.items():
            for init in gefs.init_dates(peak):
                fc = gefs.fetch(init)
                t0 = pd.Timestamp(init)
                peaks, pmax = {}, {}
                for m in sorted(fc["member"].unique()):
                    q = model.predict(scenario(data, fc, m, t0, temp)).loc[t0: t0 + pd.Timedelta(days=gefs.LEAD_DAYS - 1)]
                    peaks[m] = q.max()
                    if pf is not None:
                        pmax[m] = pf.predict(q).max()
                pk = pd.Series(peaks)
                rows.append({
                    "model": n, "temp": temp, "event": ev, "init": init, "days_before_peak": (pd.Timestamp(peak) - t0).days,
                    "members": len(pk), "prob_flood": float((pk >= thr).mean()),
                    "peak_q_p10": pk.quantile(0.1), "peak_q_median": pk.median(), "peak_q_p90": pk.quantile(0.9),
                    "model_threshold": thr, "obs_peak": float(data.y.loc[peak]),
                    "daily_max_median": pd.Series(pmax).median() if pmax else np.nan,
                    "obs_daily_max": float(data.qmax.loc[peak]) if data.qmax is not None else np.nan,
                    "gefs_basin_precip_mm": float(fc.groupby(["member", "point"])["precip_mm"].sum().mean()),
                })
                log.info("  %s init %s (D-%d): P(flood)=%.0f%%", ev, init, rows[-1]["days_before_peak"],
                         100 * rows[-1]["prob_flood"])
    out = pd.DataFrame(rows)
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_csv(config.DATA_PROCESSED / f"hindcast_{temp}.csv", index=False)
    return out
