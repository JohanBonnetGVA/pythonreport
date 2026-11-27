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


def forecast(conditions: pd.DataFrame, gr4j: pd.DataFrame) -> pd.DataFrame:
    """Join the conditions hindcast (P per level) and the GR4J hindcast (prob_flood) per init."""
    g = gr4j[["event", "init", "prob_flood"]].assign(init=lambda x: pd.to_datetime(x["init"]).dt.date)
    c = conditions.assign(init=lambda x: pd.to_datetime(x["init"]).dt.date)
    m = c.merge(g, on=["event", "init"])
    m["forecast"] = [forecast_level(*r) for r in m[["P_watch", "P_warning", "P_alert", "prob_flood"]].to_numpy()]
    m["color"] = m["forecast"].map(COLORS)
    return m[["event", "init", "days_before_peak", "P_watch", "P_warning", "P_alert", "prob_flood", "forecast", "color"]]
