import numpy as np
import pandas as pd

from inondation_etrembieres import metrics
from inondation_etrembieres.dataset import FEATURES, Dataset
from inondation_etrembieres.manager import ModelManager, Split
from inondation_etrembieres.models.gr4j_snow import GR4JSnow


def synthetic(years=("2014", "2016")) -> Dataset:
    idx = pd.date_range(f"{years[0]}-01-01", f"{years[1]}-12-31", freq="D", name="time")
    rng = np.random.default_rng(0)
    cols = {}
    for p in ("a", "b"):
        temp = 5 + 10 * np.sin(2 * np.pi * (idx.day_of_year - 110) / 365) + rng.normal(0, 2, len(idx))
        precip = rng.gamma(0.4, 10, len(idx))
        rain = np.where(temp > 0, precip, 0)
        for v in FEATURES:
            cols[(p, v)] = 0.0
        cols[(p, "temperature_2m_mean")], cols[(p, "precipitation_sum")], cols[(p, "rain_sum")] = temp, precip, rain
    w = pd.DataFrame(cols, index=idx)
    w.columns = pd.MultiIndex.from_tuples(w.columns)
    d = Dataset(weather=w, y=pd.Series(0.0, index=idx))
    d.y = GR4JSnow()._sim(d, [400, -1, 100, 2, 4, 0])  # truth
    return d


def test_gr4j_recovers_synthetic():
    d = synthetic()
    m = GR4JSnow()
    m.fit(d, d.y.loc[:"2015"].index, d.y.loc["2016"].index)
    assert metrics.nse(d.y.loc["2016"], m.predict(d).loc["2016"]) > 0.9


def test_manager_compare():
    d = synthetic()
    split = Split(train=("2014-01-01", "2014-12-31"), val=("2015-01-01", "2015-06-30"), test=("2015-07-01", "2016-12-31"))
    mm = ModelManager(d, split=split, names=("persistence", "ridge"))
    mm.run()
    c = mm.compare()
    assert set(c.index) == {"persistence", "ridge"} and "kge_test" in c


def test_lstm_runs():
    import pytest
    pytest.importorskip("torch")
    from inondation_etrembieres.models.lstm import LSTM
    d = synthetic()
    m = LSTM(hidden=8, epochs=2)
    m.fit(d, d.y.loc[:"2015"].index, d.y.loc["2016"].index)
    assert m.predict(d).loc["2016"].notna().all()


def test_peak_factor_grows_with_rise():
    from inondation_etrembieres.peak import PeakFactor
    idx = pd.date_range("2020-01-01", periods=400, freq="D")
    rng = np.random.default_rng(1)
    mean = pd.Series(50 * np.exp(rng.normal(0, 0.5, 400)), index=idx)
    rise = (mean / mean.shift(1)).fillna(1)
    qmax = mean * 1.2 * rise.clip(lower=1) ** 0.3
    pf = PeakFactor().fit(mean, qmax)
    assert pf.coef[2] > 0 and (pf.predict(mean) >= mean).all()


def test_condition_levels():
    from inondation_etrembieres import conditions as C
    z = pd.DataFrame({"rain_3d": [1.0, 3.0, 4.5, 6.0, 6.0], "wetness": [1, 1, 1, 1, 0.0],
                      "snow": 0.0, "warming": 0.0, "rain_on_snow": [1, 1, 1, 1, 1.0]})
    assert C.level(z).tolist() == [0, 1, 2, 3, 0]  # last: dry catchment → no flood conditions
