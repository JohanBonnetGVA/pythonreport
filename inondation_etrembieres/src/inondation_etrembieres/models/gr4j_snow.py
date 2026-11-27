"""GR4J (Perrin et al., 2003) + degree-day snow per elevation point (CemaNeige-like).

Parameters: x1 production store (mm), x2 exchange (mm), x3 routing store (mm), x4 UH base (d),
ddf melt factor (mm/°C/d), t0 melt temperature (°C). Output in mm/d, scaled to m³/s by catchment area.
"""
import numpy as np
import pandas as pd
from scipy.optimize import differential_evolution

from .. import metrics
from ..dataset import Dataset, oudin_pet
from .base import Model

try:
    from numba import njit
except ImportError:  # pragma: no cover
    def njit(f=None, **_):
        return f if f else (lambda g: g)

AREA_KM2 = 1664.0  # Arve at Reignier-Ésery/Arthaz (to confirm on Hydroportail station sheet)
BOUNDS = [(50, 2000), (-10, 5), (10, 500), (0.5, 6), (1, 8), (-2, 2)]
WARMUP = 365


@njit(cache=True)
def _snow(rain, solid, temp, ddf, t0):
    n, k = rain.shape
    swe = np.zeros(k)
    out = np.zeros(n)
    for t in range(n):
        acc = 0.0
        for j in range(k):
            swe[j] += solid[t, j]
            melt = min(swe[j], ddf * max(temp[t, j] - t0, 0.0))
            swe[j] -= melt
            acc += rain[t, j] + melt
        out[t] = acc / k
    return out


@njit(cache=True)
def _sh1(t, x4):
    return 0.0 if t <= 0 else (1.0 if t >= x4 else (t / x4) ** 2.5)


@njit(cache=True)
def _sh2(t, x4):
    if t <= 0:
        return 0.0
    if t <= x4:
        return 0.5 * (t / x4) ** 2.5
    if t < 2 * x4:
        return 1 - 0.5 * (2 - t / x4) ** 2.5
    return 1.0


@njit(cache=True)
def _uh(x4):
    n1, n2 = int(np.ceil(x4)), int(np.ceil(2 * x4))
    uh1, uh2 = np.zeros(n1), np.zeros(n2)
    for i in range(n1):
        uh1[i] = _sh1(i + 1, x4) - _sh1(i, x4)
    for i in range(n2):
        uh2[i] = _sh2(i + 1, x4) - _sh2(i, x4)
    return uh1, uh2


@njit(cache=True)
def _gr4j(p, e, x1, x2, x3, x4):
    uh1, uh2 = _uh(x4)
    q1, q2 = np.zeros(len(uh1)), np.zeros(len(uh2))
    s, r = 0.5 * x1, 0.5 * x3
    out = np.zeros(len(p))
    for t in range(len(p)):
        if p[t] >= e[t]:
            pn = p[t] - e[t]
            th = np.tanh(pn / x1)
            ps = x1 * (1 - (s / x1) ** 2) * th / (1 + s / x1 * th)
            s += ps
        else:
            en, pn, ps = e[t] - p[t], 0.0, 0.0
            th = np.tanh(en / x1)
            s -= s * (2 - s / x1) * th / (1 + (1 - s / x1) * th)
        perc = s * (1 - (1 + (4 / 9 * s / x1) ** 4) ** -0.25)
        s -= perc
        pr = perc + pn - ps
        q1[:-1], q1[-1] = q1[1:] + uh1[:-1] * 0.9 * pr, uh1[-1] * 0.9 * pr
        q2[:-1], q2[-1] = q2[1:] + uh2[:-1] * 0.1 * pr, uh2[-1] * 0.1 * pr
        f = x2 * (r / x3) ** 3.5
        r = max(0.0, r + q1[0] + f)
        qr = r * (1 - (1 + (r / x3) ** 4) ** -0.25)
        r -= qr
        out[t] = qr + max(0.0, q2[0] + f)
    return out


class GR4JSnow(Model):
    name = "gr4j_snow"

    def __init__(self, seed: int = 0):
        self.params: np.ndarray | None = None
        self.seed = seed

    @staticmethod
    def _forcing(data: Dataset):
        b = data.basin().ffill().fillna(0)
        e = oudin_pet(b["temp"].mean(axis=1), data.lat).to_numpy()
        return b.index, b["rain"].to_numpy(), b["solid"].to_numpy(), b["temp"].to_numpy(), e

    @staticmethod
    def _run(forcing, params) -> pd.Series:
        idx, rain, solid, temp, e = forcing
        x1, x2, x3, x4, ddf, t0 = params
        q_mm = _gr4j(_snow(rain, solid, temp, ddf, t0), e, x1, x2, x3, x4)
        return pd.Series(q_mm * AREA_KM2 / 86.4, index=idx)

    def _sim(self, data: Dataset, params) -> pd.Series:
        return self._run(self._forcing(data), params)

    def fit(self, data: Dataset, train: pd.Index, val: pd.Index) -> None:
        idx, forcing = train[WARMUP:], self._forcing(data)
        obs = data.y.loc[idx]
        obj = lambda x: -metrics.kge(obs, self._run(forcing, x).loc[idx])
        self.params = differential_evolution(obj, BOUNDS, seed=self.seed, maxiter=60, tol=1e-4, polish=True).x

    def predict(self, data: Dataset) -> pd.Series:
        return self._sim(data, self.params)
