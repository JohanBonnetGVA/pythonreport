# Inondation Étrembières — Specification

## 1. Objective
Show the **probability of flooding of the Arve at Étrembières** for the coming days, based on the weather forecast.
The model is trained on past data, including the reference floods of **November 2023** and **2015**.

## 2. Scope
- **Location:** Étrembières (Haute-Savoie, FR), on the Arve river, upstream of Geneva.
- **Horizon:** D+0 to D+7, daily.
- **Output:** a flood probability (0–100 %) per day, with a risk level (low / moderate / high), as data (CSV/Parquet).
- **Out of scope (for now):** HTML, charts, dashboard.

## 3. Input variables
| Variable | Role | Source (to confirm) |
|---|---|---|
| Precipitation (mm/day, 24–72 h totals) | main driver | Open-Meteo Forecast / ERA5 archive |
| Snow cover / snow depth, snowmelt | rain-on-snow, melt contribution | Open-Meteo (ERA5-Land) |
| Soil moisture (0–7 cm, 7–28 cm) | catchment saturation | Open-Meteo (ERA5-Land) |
| Temperature (0 °C isotherm proxy) | rain vs snow | Open-Meteo |
| River discharge (m³/s) | target + current state | BAFU station **2170 Arve – Genève Bout du Monde** (CH, since 1904, just downstream); Vigicrues/Hydroportail station **Reignier-Ésery** (FR, just upstream) |

Catchment variables are taken as a **basin average** over the upper Arve (Chamonix → Étrembières), not just at the town.

## 4. Reference events
| Event | Peak (Genève Bout du Monde) | Notes |
|---|---|---|
| 15 Nov 2023 | ~1010 m³/s (record since 1904) | Successive storms on saturated soils; ~1200 m³/s est. at Reignier-Ésery |
| End Apr – 2 May 2015 | ~905 m³/s (2 May) | Heavy rain from end of April + snowmelt; lower than 2023 |

These events are used to **validate** the model (did it predict a high probability?). Two events are not enough to train on by themselves.

## 5. Modelling approach
1. **Target:** flood = daily peak discharge above a threshold (e.g. vigilance level or Q10 return period, to be set from gauge data).
2. **Training data:** full daily history (≥ 20 years, ERA5 + gauge), not only the 2 events.
3. **Features:** cumulative precipitation over 1/3/5 days, snow depth change, soil moisture, temperature, previous-day discharge.
4. **Model:** logistic regression as the baseline, then gradient boosting (e.g. LightGBM), with calibrated probabilities.
5. **Forecast probability:** run the model on every member of the **ensemble weather forecast** (Open-Meteo Ensemble API). Probability = the share of members that exceed the flood threshold.
6. **Validation:** time-based split (no random shuffle); report Brier score, ROC-AUC and recall on flood days; hindcast Nov 2023 and 2015.

> **Best practice:**
> - Floods are rare, so the classes are imbalanced. Use class weights and judge the model with Brier score and recall, not accuracy.
> - Split train/test by time so the model never sees future data during training.
> - Take the probability from ensemble members so it reflects real forecast uncertainty.

## 6. Output
- `data/processed/forecast_probability.csv`: date, rain (mm), snow depth, soil moisture, predicted discharge, probability, level.
- Saved model + metrics (`data/models/`).

## 7. Project structure
```
inondation_etrembieres/
├── SPEC.md
├── pyproject.toml
├── data/                 # raw + processed (git-ignored except .gitkeep)
├── src/inondation_etrembieres/
│   ├── fetch_weather.py  # Open-Meteo forecast + archive
│   ├── fetch_discharge.py# Hydroportail / BAFU
│   ├── features.py
│   ├── model.py          # train, calibrate, save
│   └── predict.py        # ensemble forecast -> probability
└── tests/
```

## 8. Open questions
- Flood threshold: vigilance level or return period (e.g. Q10 at station 2170)?
- Reignier-Ésery station code and data access (Hydroportail export / API)?
