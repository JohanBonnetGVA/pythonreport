"""Two alerts.

HARD (observed, deterministic): what has already happened — observed rain/wetness/snow
conditions (σ indicators, ERA5) and the observed flow at Reignier. Certain, short lead.
  yellow  conditions ≥ watch
  orange  conditions ≥ warning AND flow reacting (Q/Q(t−1) ≥ 1.5 or Q ≥ 300 m³/s)
  red     conditions ≥ watch AND Q ≥ 450 m³/s (daily mean)
  1990–2026: orange 13 episodes (6 → Q ≥ 400), red 4 episodes (4 → Q ≥ 400).

FORECAST (probabilistic): current state + GEFS days 1–7, per ensemble member:
  P_warning / P_alert  share of members reaching condition level warning / alert
  P_gr4j               share of members whose GR4J flow exceeds the model flood threshold
  yellow  P_watch ≥ 50 % or P_warning ≥ 20 %
  orange  P_warning ≥ 50 % or P_gr4j ≥ 50 %
  red     P_alert ≥ 50 % or (P_warning ≥ 70 % and P_gr4j ≥ 50 %)

OUTLOOK (used to LIFT a watch, not to raise alerts):
  very unlikely  P(watch, days 1–3) ≤ 10 % and P(watch, days 4–7) ≤ 10 % → a hard yellow becomes green
                 ("no flood conditions in the next 7 days")
  Days 8–16 are shown as a trend only: in the 2015/2023 hindcasts GEFS showed ≤ 10 % at 10–16 days
  before both floods, so it cannot give an all-clear that far ahead.
  (orange/red are never lifted by a forecast: they are confirmed by observed flow)
"""
import pandas as pd

from . import conditions as C

COLORS = {0: "green", 1: "yellow", 2: "orange", 3: "red"}
HARD = {"rise": 1.5, "q_react": 300.0, "q_red": 450.0}


def hard(data) -> pd.DataFrame:
    """Daily hard alert from observed weather and flow."""
    z = C.zscores(C.indicators(data.weather))
    lv, q = C.level(z), data.y
    rise = q / q.shift(1)
    a = pd.Series(0, index=q.index)
    a[lv >= 1] = 1
    a[(lv >= 2) & ((rise >= HARD["rise"]) | (q >= HARD["q_react"]))] = 2
    a[(lv >= 1) & (q >= HARD["q_red"])] = 3
    return pd.DataFrame({"hard": a, "color": a.map(COLORS), "condition_level": lv, "q_mean": q,
                         "rise": rise, **{f"{c}_sigma": z[c] for c in ("rain_3d", "wetness", "warming")},
                         "rain_on_snow": z["rain_on_snow"], "snow_m": z["snow_m"]})


def forecast_level(p_watch: float, p_warning: float, p_alert: float, p_gr4j: float) -> int:
    if p_alert >= 0.5 or (p_warning >= 0.7 and p_gr4j >= 0.5):
        return 3
    if p_warning >= 0.5 or p_gr4j >= 0.5:
        return 2
    if p_watch >= 0.5 or p_warning >= 0.2:
        return 1
    return 0


OUTLOOK = {"p_watch": 0.10}


def outlook(p_watch_d1_3: float, p_watch_d4_7: float) -> str:
    """All-clear for the next 7 days only (days 8–16 have no all-clear skill)."""
    return "very unlikely" if max(p_watch_d1_3, p_watch_d4_7) <= OUTLOOK["p_watch"] else "possible"


def trend(p_watch_d8_16: float) -> str:
    return "signal" if p_watch_d8_16 >= 0.3 else "quiet"


def final(hard_level: int, outlook_: str) -> int:
    """Combined status: a forecast can only lift a watch (yellow), never an observed orange/red."""
    return 0 if hard_level == 1 and outlook_ == "very unlikely" else hard_level


def forecast(conditions: pd.DataFrame, gr4j: pd.DataFrame) -> pd.DataFrame:
    """Join the conditions hindcast (P per level) and the GR4J hindcast (prob_flood) per init."""
    g = gr4j[["event", "init", "prob_flood"]].assign(init=lambda x: pd.to_datetime(x["init"]).dt.date)
    c = conditions.assign(init=lambda x: pd.to_datetime(x["init"]).dt.date)
    m = c.merge(g, on=["event", "init"])
    m["forecast"] = [forecast_level(*r) for r in m[["P_watch", "P_warning", "P_alert", "prob_flood"]].to_numpy()]
    m["color"] = m["forecast"].map(COLORS)
    m["outlook_7d"] = [outlook(a, b) for a, b in m[["P_watch_d1_3", "P_watch_d4_7"]].to_numpy()]
    m["trend_8_16"] = m["P_watch_d8_16"].map(trend)
    wins = [c for c in m.columns if c.startswith("P_watch_d")]
    return m[["event", "init", "days_before_peak", *wins, "P_warning", "P_alert", "prob_flood", "forecast", "color", "outlook_7d", "trend_8_16"]]
