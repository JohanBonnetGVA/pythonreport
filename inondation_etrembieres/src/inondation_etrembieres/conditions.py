"""Flood-condition indicators: how unusual is each ingredient, in standard deviations (σ)?

Each indicator is a basin value turned into a z-score against its day-of-year climatology
(1990–2016, ±15-day window), so "+2σ" means "as unusual as ~1 day in 40 for the season".

Ingredients (the 2015 and 2023 mechanisms):
  rain_3d      3-day basin precipitation (mean over points)
  wetness      antecedent precipitation index API = Σ k^i·P(t−i), k = 0.9 (catchment wetness;
               computed from precipitation, so forecast rain also wets the soil)
  snow         snow depth at snow points (≥ 1100 m)
  warming      2-day temperature rise at snow points (melt / rising rain-snow limit)
  rain_on_snow share of snow points with ≥ 0.2 m snow, T > 0 °C and rain > 5 mm (0–1, not a z-score)

Flood conditions = rain_3d ≥ 2σ AND wetness ≥ 0.5σ AND (rain_on_snow ≥ 0.5 OR (warming ≥ 1σ AND snow ≥ 0.2 m)).
z-scores are seasonal (day-of-year): +1σ warming in December is a smaller absolute rise than in May.
"""
import numpy as np
import pandas as pd

from . import config

SNOW_MIN_ELEV = 1100
SNOW_MIN_DEPTH = 0.2  # m: below this a snowpack adds negligible melt water
CLIM = ("1990-01-01", "2016-12-31")
API_K = 0.9
RULE = {"rain_3d": 2.0, "wetness": 0.5, "rain_on_snow": 0.5, "warming": 1.0}


def indicators(w: pd.DataFrame) -> pd.DataFrame:
    """Raw basin indicators from a (point, variable) daily weather frame."""
    snow_pts = [p.name for p in config.POINTS if p.elevation >= SNOW_MIN_ELEV]
    x = lambda v, pts=None: w.xs(v, axis=1, level=1)[pts] if pts else w.xs(v, axis=1, level=1)
    precip = x("precipitation_sum").mean(axis=1)
    t_snow = x("temperature_2m_mean", snow_pts)
    snow = x("snow_depth_last", snow_pts)
    rain = x("rain_sum", snow_pts)
    ros = ((snow.shift(1) >= SNOW_MIN_DEPTH) & (t_snow > 0) & (rain > 5)).mean(axis=1)
    return pd.DataFrame({
        "rain_3d": precip.rolling(3, min_periods=1).sum(),
        "wetness": precip.shift(1).fillna(0).ewm(alpha=1 - API_K, adjust=False).mean() / (1 - API_K),
        "snow": snow.mean(axis=1),
        "snow_m": snow.mean(axis=1),  # raw depth (not standardised): gates the warming branch
        "warming": t_snow.mean(axis=1).diff(2),
        "rain_on_snow": ros,
    })


def climatology(ind: pd.DataFrame, clim: tuple[str, str] = CLIM) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Day-of-year mean/std per indicator (±15-day window over the climatology period)."""
    ref = ind.loc[clim[0]: clim[1]]
    rdoy = ref.index.day_of_year.to_numpy()
    out = {}
    for c in ind.columns.drop(["rain_on_snow", "snow_m"]):
        v, mu, sd = ref[c].to_numpy(), np.empty(367), np.empty(367)
        for d in range(1, 367):
            win = np.abs(((rdoy - d + 183) % 366) - 183) <= 15
            mu[d], sd[d] = np.nanmean(v[win]), np.nanstd(v[win]) or 1.0
        out[c] = (mu, sd)
    return out


def zscores(ind: pd.DataFrame, clim=None) -> pd.DataFrame:
    """z-scores vs day-of-year climatology; rain_on_snow kept as a share (0–1)."""
    clim = clim if isinstance(clim, dict) else climatology(ind, clim or CLIM)
    doy = ind.index.day_of_year.to_numpy()
    out = pd.DataFrame({c: (ind[c].to_numpy() - mu[doy]) / sd[doy] for c, (mu, sd) in clim.items()}, index=ind.index)
    out["rain_on_snow"] = ind["rain_on_snow"]
    out["snow_m"] = ind["snow_m"]
    return out


def flag(z: pd.DataFrame, rule: dict = RULE) -> pd.Series:
    return ((z["rain_3d"] >= rule["rain_3d"]) & (z["wetness"] >= rule["wetness"])
            & ((z["rain_on_snow"] >= rule["rain_on_snow"])
               | ((z["warming"] >= rule["warming"]) & (z["snow_m"] >= SNOW_MIN_DEPTH))))  # no melt without snow


LEVELS = {1: "watch (rain ≥ 2σ)", 2: "warning (rain ≥ 4σ)", 3: "alert (rain ≥ 5σ)"}


def level(z: pd.DataFrame) -> pd.Series:
    """0 none, 1 watch, 2 warning, 3 alert: the combined rule with rain at 2 / 4 / 5 σ."""
    return sum(flag(z, {**RULE, "rain_3d": r}).astype(int) for r in (2.0, 4.0, 5.0))


def forecast(data, fc: pd.DataFrame, init: pd.Timestamp, clim: dict, lead_days: int | None = None) -> pd.DataFrame:
    """Per member: max level and indicator z-scores over the forecast days.

    GEFS gives rain/temperature; snow depth is persisted from the observed state on init−1
    (GEFS has none); wetness follows the forecast rain."""
    from .hindcast import scenario
    from .sources.gefs import LEAD_DAYS
    lead_days = lead_days or LEAD_DAYS
    rows = []
    for m in sorted(fc["member"].unique()):
        w = scenario(data, fc, m, init).weather
        state = w.loc[:, (slice(None), ["snow_depth_last"])]
        w.loc[:, state.columns] = state.ffill()
        z = zscores(indicators(w).iloc[-120:], clim).loc[init: init + pd.Timedelta(days=lead_days - 1)]
        lv = level(z)
        rows.append({"member": m, "level": int(lv.max()), "day_of_max": lv.idxmax().date(),
                     **{f"max_{c}": z[c].max() for c in z.columns}})
    return pd.DataFrame(rows)


def skill(flags: pd.Series, q: pd.Series, threshold: float, window: int = 2) -> dict:
    """Event-based check: is a flood (Q ≥ threshold) within `window` days after a flagged day?"""
    flood = q >= threshold
    flood_ahead = flood.rolling(window + 1).max().shift(-window).fillna(0).astype(bool)
    flagged_ahead = flags.rolling(window + 1, min_periods=1).max().astype(bool)
    hits = int((flags & flood_ahead).sum())
    return {
        "flagged_days": int(flags.sum()), "flagged_days_with_flood": hits,
        "flood_days": int(flood.sum()), "flood_days_flagged_before": int((flood & flagged_ahead).sum()),
        "high_flow_days_after_flag (Q≥400)": int((flags & (q.rolling(window + 1).max().shift(-window) >= 400)).sum()),
    }
