"""Reference only: tomorrow = today. Uses observed Q, so it is a 1-day nowcast, not a simulation."""
import pandas as pd

from ..dataset import Dataset
from .base import Model


class Persistence(Model):
    name = "persistence"

    def fit(self, data: Dataset, train: pd.Index, val: pd.Index) -> None:
        pass

    def predict(self, data: Dataset) -> pd.Series:
        return data.y.shift(1)
