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


def flow_weights(obs, power: float) -> np.ndarray:
    """High-flow weights w = (Q / mean Q)^power, normalised to mean 1 (power 0 = plain MSE)."""
    o = np.asarray(obs, dtype=float)
    w = np.nan_to_num(o / np.nanmean(o), nan=0.0) ** power
    return w / w.mean()


def nse_weighted(obs, sim, power: float = 2.0) -> float:
    """NSE with high-flow weights: errors on floods count more."""
    o, s = _align(obs, sim)
    w = flow_weights(o, power)
    return float(1 - (w * (o - s) ** 2).sum() / (w * (o - o.mean()) ** 2).sum())


def event(obs: pd.Series, sim: pd.Series, threshold: float) -> dict:
    """Peak magnitude, timing and threshold hit over an event window."""
    o, s = obs.dropna(), sim.reindex(obs.dropna().index)
    return {
        "peak_obs": o.max(), "peak_sim": s.max(),
        "peak_err_pct": 100 * (s.max() - o.max()) / o.max(),
        "timing_days": (s.idxmax() - o.idxmax()).days,
        "obs_flood": bool(o.max() >= threshold), "sim_flood": bool(s.max() >= threshold),
    }
