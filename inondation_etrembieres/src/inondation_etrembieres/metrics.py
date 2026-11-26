"""Hydrological scores."""
import numpy as np
import pandas as pd


def _align(obs: pd.Series, sim: pd.Series):
    m = obs.notna() & sim.notna()
    return obs[m].to_numpy(), sim[m].to_numpy()


def nse(obs, sim) -> float:
    o, s = _align(obs, sim)
    return float(1 - ((o - s) ** 2).sum() / ((o - o.mean()) ** 2).sum())


def kge(obs, sim) -> float:
    o, s = _align(obs, sim)
    r = np.corrcoef(o, s)[0, 1]
    return float(1 - np.sqrt((r - 1) ** 2 + (s.std() / o.std() - 1) ** 2 + (s.mean() / o.mean() - 1) ** 2))


def event(obs: pd.Series, sim: pd.Series, threshold: float) -> dict:
    """Peak magnitude, timing and threshold hit over an event window."""
    o, s = obs.dropna(), sim.reindex(obs.dropna().index)
    return {
        "peak_obs": o.max(), "peak_sim": s.max(),
        "peak_err_pct": 100 * (s.max() - o.max()) / o.max(),
        "timing_days": (s.idxmax() - o.idxmax()).days,
        "obs_flood": bool(o.max() >= threshold), "sim_flood": bool(s.max() >= threshold),
    }
