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
