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
