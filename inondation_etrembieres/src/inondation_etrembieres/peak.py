"""Daily peak from daily mean: log(max/mean) = a + b·log(mean) + c·log(rise).

The daily-mean series is complete since 1990, daily max only since 2019 (Reignier DREAL),
so models simulate the mean and this peak factor converts it to the daily maximum.
rise = mean_t / mean_{t-1}: fast rises (short flood bursts) have larger peak/mean ratios.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


def _X(mean: pd.Series) -> np.ndarray:
    rise = (mean / mean.shift(1)).clip(lower=0.2, upper=10).fillna(1.0)
    return np.column_stack([np.ones(len(mean)), np.log(mean.clip(lower=1)), np.log(rise)])


@dataclass
class PeakFactor:
    coef: np.ndarray | None = None

    def fit(self, mean: pd.Series, qmax: pd.Series) -> "PeakFactor":
        m = mean.notna() & qmax.notna() & (mean > 0)
        X, y = _X(mean)[m.to_numpy()], np.log(qmax[m] / mean[m]).to_numpy()
        w = mean[m].to_numpy() / mean[m].mean()  # weight high flows (they matter for floods)
        self.coef = np.linalg.lstsq(X * w[:, None] ** 0.5, y * w ** 0.5, rcond=None)[0]
        return self

    def predict(self, mean: pd.Series) -> pd.Series:
        return mean * np.exp(np.clip(_X(mean) @ self.coef, 0, None))  # max ≥ mean
