import pandas as pd

from inondation_etrembieres.sources import discharge, weather


def test_to_daily_snowmelt():
    idx = pd.date_range("2023-11-01", periods=48, freq="h")
    h = pd.DataFrame({c: 1.0 for c in weather.HOURLY}, index=idx)
    h["snow_depth"] = [1.0] * 24 + [0.6] * 24
    d = weather.to_daily(h)
    assert d["precipitation_sum"].tolist() == [24.0, 24.0]
    assert abs(d["snow_depth_loss"].iloc[1] - 0.4) < 1e-9


def test_load_csv(tmp_path):
    f = tmp_path / "2170.csv"
    f.write_text("Zeitstempel;Wert\n15.11.2023 07:00;980\n15.11.2023 08:00;1010\n")
    s = discharge.load_csv(f, sep=";")
    assert discharge.daily_peak(s)["discharge_max"].iloc[0] == 1010


def test_store_roundtrip(tmp_path):
    from inondation_etrembieres import store
    (tmp_path / "weather").mkdir()
    (tmp_path / "discharge").mkdir()
    idx = pd.date_range("2023-11-14", periods=2, freq="D", name="time")
    pd.DataFrame({"precipitation_sum": [40.0, 60.0]}, index=idx).to_parquet(tmp_path / "weather" / "flaine.parquet")
    pd.DataFrame({"discharge_max": [800.0, 1010.0]}, index=idx).to_parquet(tmp_path / "discharge" / "2170_daily.parquet")
    db = tmp_path / "t.duckdb"
    store.build(db, raw=tmp_path)
    df = store.query("""SELECT w.point, d.discharge_max FROM weather_daily w
                        JOIN discharge_daily d USING (time) WHERE w.point = $p ORDER BY time""", db, p="flaine")
    assert df["discharge_max"].tolist() == [800.0, 1010.0]


def test_fetch_point_resumes_from_chunks(tmp_path, monkeypatch):
    from datetime import date
    from inondation_etrembieres import config
    calls = []

    def fake(point, a, b, s=None):
        calls.append(a)
        idx = pd.date_range(a, b + pd.Timedelta(days=1), freq="h", inclusive="left")
        return pd.DataFrame({c: 1.0 for c in weather.HOURLY}, index=idx)

    monkeypatch.setattr(weather, "DATA_RAW", tmp_path)
    monkeypatch.setattr(weather, "fetch_hourly", fake)
    pt = config.POINTS[0]
    weather.fetch_point(pt, date(2014, 1, 1), date(2017, 12, 31))
    assert len(calls) == 2
    d = weather.fetch_point(pt, date(2014, 1, 1), date(2017, 12, 31))
    assert len(calls) == 3 and len(d) == 4 * 365 + 1  # only the recent chunk is re-fetched


def test_all_null_variable_fails(monkeypatch):
    import pytest
    from datetime import date
    from inondation_etrembieres import config
    payload = {"hourly": {"time": ["2023-11-01T00:00"], **{c: [1.0] for c in weather.HOURLY}}}
    payload["hourly"]["rain"] = [None]
    monkeypatch.setattr(weather, "_get", lambda s, p: payload)
    with pytest.raises(ValueError, match="rain"):
        weather.fetch_hourly(config.POINTS[0], date(2023, 11, 1), date(2023, 11, 1), s=object())


def test_daily_sum_keeps_nan():
    idx = pd.date_range("2023-11-01", periods=24, freq="h")
    h = pd.DataFrame({c: 1.0 for c in weather.HOURLY}, index=idx)
    h["rain"] = float("nan")
    assert weather.to_daily(h)["rain_sum"].isna().all()


def test_hubeau_parse(monkeypatch):
    from datetime import date
    from inondation_etrembieres.sources import hubeau

    class R:
        def __init__(self, j): self.j = j
        def raise_for_status(self): pass
        def json(self): return self.j

    pages = [
        {"data": [{"date_obs_elab": "2023-11-14", "resultat_obs_elab": 800000.0, "libelle_qualification": "Bonne"}],
         "next": "https://x/page2"},
        {"data": [{"date_obs_elab": "2023-11-15", "resultat_obs_elab": 1200000.0, "libelle_qualification": "Douteuse"}],
         "next": None},
    ]

    class S:
        def get(self, url, params=None, timeout=None): return R(pages.pop(0))

    monkeypatch.setattr(hubeau, "session", S)
    d = hubeau.fetch_series("QIXnJ", date(2023, 11, 14), date(2023, 11, 15))
    assert d["value_m3s"].tolist() == [800.0, 1200.0]
    assert d.index[1] == pd.Timestamp("2023-11-15")
