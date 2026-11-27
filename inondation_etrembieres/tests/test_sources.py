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


def test_gefs_daily_from_buckets():
    from datetime import date
    from inondation_etrembieres.sources import gefs
    rows = []
    for s, e, v in [(0, 3, 1.0), (0, 6, 3.0), (6, 9, 2.0), (6, 12, 2.5), (12, 18, 1.0), (18, 24, 1.0), (24, 30, 4.0)]:
        rows.append(dict(member="c00", var="precip", start_h=s, end_h=e, point="flaine", value=v))
    for h, k in [(6, 273.15), (12, 275.15), (30, 280.15)]:
        rows.append(dict(member="c00", var="temp", start_h=h, end_h=h, point="flaine", value=k))
    rows.append(dict(member="c00", var="z", start_h=0, end_h=0, point="flaine", value=1500.0))
    d = gefs._to_daily(pd.DataFrame(rows), date(2023, 11, 10)).set_index("date")
    assert d.loc["2023-11-10", "precip_mm"] == 3.0 + 2.5 + 1.0 + 1.0
    assert d.loc["2023-11-11", "precip_mm"] == 4.0
    assert abs(d.loc["2023-11-10", "temp_c"] - 1.0) < 1e-9 and d["z_grid"].iloc[0] == 1500.0


def test_gefs_idx_ranges():
    from inondation_etrembieres.sources import gefs
    idx = "1:0:d=2023111000:HGT:surface:anl:ENS=low-res ctl\n2:100:d=2023111000:TMP:2 m above ground:6 hour fcst:\n3:250:d=2023111000:RH:2 m above ground:6 hour fcst:\n4:400:d=2023111000:APCP:surface:0-6 hour acc fcst:\n"
    assert gefs._ranges(idx) == [(0, 99), (100, 249), (400, None)]


def test_hindcast_scenario_splits_snow():
    from inondation_etrembieres.hindcast import scenario
    from inondation_etrembieres.dataset import Dataset
    idx = pd.date_range("2023-11-01", periods=9, freq="D", name="time")
    cols = pd.MultiIndex.from_product([["flaine"], ["precipitation_sum", "rain_sum", "temperature_2m_mean"]])
    hist = Dataset(weather=pd.DataFrame(1.0, index=idx, columns=cols), y=pd.Series(1.0, index=idx))
    fc = pd.DataFrame({"member": "c00", "point": "flaine", "date": pd.to_datetime(["2023-11-10", "2023-11-11"]),
                       "precip_mm": [10.0, 10.0], "temp_c": [5.0, -5.0], "z_grid": [1600.0, 1600.0]})
    d = scenario(hist, fc, "c00", pd.Timestamp("2023-11-10"))
    assert d.weather.index[-1] == pd.Timestamp("2023-11-11")
    assert d.weather.loc["2023-11-10", ("flaine", "rain_sum")] == 10.0   # 5 °C → rain
    assert d.weather.loc["2023-11-11", ("flaine", "rain_sum")] == 0.0    # −5 °C → snow


def test_hindcast_scenario_observed_temperature():
    from inondation_etrembieres.hindcast import scenario
    from inondation_etrembieres.dataset import Dataset
    idx = pd.date_range("2023-11-01", periods=12, freq="D", name="time")
    cols = pd.MultiIndex.from_product([["flaine"], ["precipitation_sum", "rain_sum", "temperature_2m_mean"]])
    hist = Dataset(weather=pd.DataFrame(-5.0, index=idx, columns=cols), y=pd.Series(1.0, index=idx))
    fc = pd.DataFrame({"member": "c00", "point": "flaine", "date": pd.to_datetime(["2023-11-10"]),
                       "precip_mm": [10.0], "temp_c": [5.0], "z_grid": [1600.0]})
    d = scenario(hist, fc, "c00", pd.Timestamp("2023-11-10"), temp="era5")
    assert d.weather.loc["2023-11-10", ("flaine", "temperature_2m_mean")] == -5.0
    assert d.weather.loc["2023-11-10", ("flaine", "rain_sum")] == 0.0  # observed −5 °C → snow
