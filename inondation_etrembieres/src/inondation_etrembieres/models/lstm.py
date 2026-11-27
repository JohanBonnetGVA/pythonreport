"""LSTM rainfall-runoff model (Kratzert et al., 2018 style), simulation mode. Needs `pip install .[lstm]`."""
import numpy as np
import pandas as pd

from .. import config, metrics
from ..dataset import Dataset
from .base import Model

SEQ = 365


class LSTM(Model):
    name = "lstm"

    def __init__(self, hidden: int = 64, epochs: int = 60, lr: float = 1e-3, seed: int = 0):
        self.hidden, self.epochs, self.lr, self.seed = hidden, epochs, lr, seed

    def _x(self, data: Dataset) -> np.ndarray:
        f = data.features().ffill().fillna(0)
        return ((f - self.mu) / self.sd).to_numpy(np.float32)

    def _windows(self, x, idx_pos):
        import torch
        return torch.from_numpy(np.stack([x[i - SEQ + 1: i + 1] for i in idx_pos]))

    def fit(self, data: Dataset, train: pd.Index, val: pd.Index) -> None:
        import torch
        torch.manual_seed(self.seed)
        f = data.features().loc[train]
        self.mu, self.sd = f.mean(), f.std().replace(0, 1)
        self.ymu, self.ysd = data.y.loc[train].mean(), data.y.loc[train].std()
        x = self._x(data)
        pos = lambda ix: [i for i in data.y.index.get_indexer(ix) if i >= SEQ - 1 and not np.isnan(data.y.iloc[i])]
        tr, va = pos(train), pos(val)
        y = torch.tensor(((data.y - self.ymu) / self.ysd).to_numpy(np.float32))
        w = torch.tensor(metrics.flow_weights(data.y.fillna(self.ymu), config.HIGH_FLOW_POWER).astype(np.float32))

        self.net = _Net(x.shape[1], self.hidden)
        opt = torch.optim.Adam(self.net.parameters(), lr=self.lr)
        best, state = np.inf, None
        for _ in range(self.epochs):
            self.net.train()
            for b in np.array_split(np.random.default_rng(self.seed).permutation(tr), max(1, len(tr) // 256)):
                opt.zero_grad()
                loss = torch.mean(w[b] * (self.net(self._windows(x, b)) - y[b]) ** 2)  # high-flow weighted MSE
                loss.backward()
                opt.step()
            self.net.eval()
            with torch.no_grad():
                v = torch.mean(w[va] * (self.net(self._windows(x, va)) - y[va]) ** 2).item()
            if v < best:  # early stopping on validation
                best, state = v, {k: t.clone() for k, t in self.net.state_dict().items()}
        self.net.load_state_dict(state)

    def predict(self, data: Dataset) -> pd.Series:
        import torch
        x = self._x(data)
        pos = list(range(SEQ - 1, len(x)))
        self.net.eval()
        with torch.no_grad():
            out = np.concatenate([self.net(self._windows(x, b)).numpy() for b in np.array_split(pos, max(1, len(pos) // 1024))])
        s = pd.Series(np.nan, index=data.y.index)
        s.iloc[pos] = out * self.ysd + self.ymu
        return s.clip(lower=0)


def _Net(n_in: int, hidden: int):
    import torch.nn as nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(n_in, hidden, batch_first=True)
            self.head = nn.Sequential(nn.Dropout(0.3), nn.Linear(hidden, 1))

        def forward(self, x):
            return self.head(self.lstm(x)[0][:, -1]).squeeze(-1)

    return Net()
