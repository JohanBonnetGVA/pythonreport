"""Common model interface."""
from abc import ABC, abstractmethod

import pandas as pd

from ..dataset import Dataset


class Model(ABC):
    """Simulation-mode discharge model: weather in, discharge out (no observed Q as input)."""

    name: str

    @abstractmethod
    def fit(self, data: Dataset, train: pd.Index, val: pd.Index) -> None: ...

    @abstractmethod
    def predict(self, data: Dataset) -> pd.Series:
        """Simulate over the full data index (models need the history for warm-up)."""
