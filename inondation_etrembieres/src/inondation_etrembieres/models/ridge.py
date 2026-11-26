"""Linear model on rolling weather features (can extrapolate above training peaks)."""
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..dataset import Dataset
from .base import Model

WINDOWS = (1, 3, 7, 30)


def build(data: Dataset) -> pd.DataFrame:
    f = data.features()
    parts = [f]
    for w in WINDOWS[1:]:
        flux = f.filter(regex="precipitation_sum|rain_sum|snowfall_sum|snow_depth_loss")
        parts.append(flux.rolling(w, min_periods=1).sum().add_suffix(f"__sum{w}"))
    return pd.concat(parts, axis=1).ffill().fillna(0)


class Ridge(Model):
    name = "ridge"

    def __init__(self):
        self.pipe = make_pipeline(StandardScaler(), RidgeCV(alphas=(0.1, 1, 10, 100, 1000)))

    def fit(self, data: Dataset, train: pd.Index, val: pd.Index) -> None:
        X, y = build(data).loc[train], data.y.loc[train]
        m = y.notna()
        self.pipe.fit(X[m], y[m])

    def predict(self, data: Dataset) -> pd.Series:
        X = build(data)
        return pd.Series(self.pipe.predict(X), index=X.index).clip(lower=0)
