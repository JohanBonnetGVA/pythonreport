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

OUTLOOK (16 days, used to LIFT a watch, not to raise alerts):
  very unlikely  P(any watch, days 1–16) ≤ 10 % and P_gr4j ≤ 5 %  → a hard yellow becomes green
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


OUTLOOK = {"p_watch": 0.10, "p_gr4j": 0.05}


def outlook(p_watch_any: float, p_gr4j: float) -> str:
    return "very unlikely" if p_watch_any <= OUTLOOK["p_watch"] and p_gr4j <= OUTLOOK["p_gr4j"] else "possible"


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
    m["outlook"] = [outlook(p, g) for p, g in m[["P_watch", "prob_flood"]].to_numpy()]
    wins = [c for c in m.columns if c.startswith("P_watch_d")]
    return m[["event", "init", "days_before_peak", *wins, "P_warning", "P_alert", "prob_flood", "forecast", "color", "outlook"]]
