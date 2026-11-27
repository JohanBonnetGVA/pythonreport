"""Model manager: train every registered model on the same split, then compare.

Split (time-based, no shuffle):
  train  1990–2016  (includes the Apr–May 2015 flood)
  val    2017–2021  (early stopping / model choice)
  test   2022–2023  (Nov 2023 flood never seen in training)
"""
import logging
from dataclasses import dataclass, field

import pandas as pd

from . import config, metrics
from .dataset import Dataset
from .models.base import Model
from .models.gr4j_snow import GR4JSnow
from .models.persistence import Persistence
from .models.ridge import Ridge

log = logging.getLogger(__name__)


def registry() -> dict[str, type[Model]]:
    reg: dict[str, type[Model]] = {m.name: m for m in (Persistence, Ridge, GR4JSnow)}
    try:
        import torch  # noqa: F401
        from .models.lstm import LSTM
        reg[LSTM.name] = LSTM
    except ImportError:
        log.warning("torch not installed: LSTM skipped (pip install .[lstm])")
    return reg


@dataclass(frozen=True)
class Split:
    train: tuple[str, str] = ("1990-01-01", "2016-12-31")
    val: tuple[str, str] = ("2017-01-01", "2021-12-31")
    test: tuple[str, str] = ("2022-01-01", "2023-12-31")


EVENTS = {
    "2015 (train)": ("2015-04-20", "2015-05-15"),
    "2023 (test)": ("2023-11-01", "2023-11-30"),
}


@dataclass
class ModelManager:
    data: Dataset
    split: Split = Split()
    names: tuple[str, ...] | None = None
    models: dict[str, Model] = field(default_factory=dict)
    sims: pd.DataFrame | None = None

    def _ix(self, period: tuple[str, str]) -> pd.Index:
        return self.data.y.loc[period[0]: period[1]].index

    def run(self) -> pd.DataFrame:
        reg = registry()
        for name in self.names or reg:
            log.info("fit %s", name)
            m = reg[name]()
            m.fit(self.data, self._ix(self.split.train), self._ix(self.split.val))
            self.models[name] = m
        self.sims = pd.DataFrame({n: m.predict(self.data) for n, m in self.models.items()})
        self.sims.insert(0, "observed", self.data.y)
        return self.sims

    def compare(self, threshold: float | None = None) -> pd.DataFrame:
        """One row per model: NSE/KGE per period + peak scores per flood event."""
        threshold = threshold or config.flood_threshold(self.data.y)
        rows = []
        for n in self.models:
            r = {"model": n}
            for p in ("train", "val", "test"):
                ix = self._ix(getattr(self.split, p))
                r[f"nse_{p}"] = metrics.nse(self.data.y.loc[ix], self.sims[n].loc[ix])
                r[f"kge_{p}"] = metrics.kge(self.data.y.loc[ix], self.sims[n].loc[ix])
            for ev, (a, b) in EVENTS.items():
                if self.data.y.loc[a:b].dropna().empty:
                    continue
                for k, v in metrics.event(self.data.y.loc[a:b], self.sims[n].loc[a:b], threshold).items():
                    r[f"{ev} {k}"] = v
            r.update(self._relative_flood(n, threshold))
            rows.append(r)
        return pd.DataFrame(rows).set_index("model").sort_values("kge_test", ascending=False)

    def _relative_flood(self, n: str, threshold: float) -> dict:
        """Judge each model on its own scale (removes its systematic peak bias).

        - rel_2015: flood if the model's 2023 peak ≥ the model's own 2015 peak.
        - qmap: model threshold = model quantile matching the observed threshold's
          quantile over the training period (usable for forecasts, not tied to one event).
        """
        sim, obs = self.sims[n], self.data.y
        (a15, b15), (a23, b23) = EVENTS["2015 (train)"], EVENTS["2023 (test)"]
        p15, p23 = sim.loc[a15:b15].max(), sim.loc[a23:b23].max()
        tr, te = self._ix(self.split.train), self._ix(self.split.test)
        q = float((obs.loc[tr].dropna() < threshold).mean())
        thr_q = float(sim.loc[tr].dropna().quantile(q))
        return {
            "ratio_2023_2015 sim": p23 / p15, "ratio_2023_2015 obs": obs.loc[a23:b23].max() / obs.loc[a15:b15].max(),
            "rel_2015 flood_2023": bool(p23 >= p15),
            "qmap threshold": thr_q, "qmap flood_2015": bool(p15 >= thr_q), "qmap flood_2023": bool(p23 >= thr_q),
            "qmap false_alarm_days_test": int(((sim.loc[te] >= thr_q) & (obs.loc[te] < threshold)).sum()),
        }

    def save(self) -> None:
        out = config.DATA_PROCESSED
        out.mkdir(parents=True, exist_ok=True)
        self.sims.to_parquet(out / "simulations.parquet")
        self.compare().to_csv(out / "model_comparison.csv")
        for ev, (a, b) in EVENTS.items():
            self.sims.loc[a:b].round(1).to_csv(out / f"event_{ev.split()[0]}.csv")
