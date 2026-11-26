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
| River discharge (m³/s) | target + current state | BAFU station **2170 Arve – Genève Bout du Monde** (CH, since 1904, just downstream) |

Catchment variables are taken as a **basin average** over the upper Arve (Chamonix → Étrembières), not just at the town.

## 4. Reference events
| Event | Peak (Genève Bout du Monde) | Notes |
|---|---|---|
| 15 Nov 2023 | ~1010 m³/s (record since 1904) | Successive storms on saturated soils; ~1200 m³/s est. at Reignier-Ésery |
| End Apr – 2 May 2015 | ~905 m³/s (2 May) | Heavy rain from end of April + snowmelt; lower than 2023 |

These events are used to **validate** the model (did it predict a high probability?). Two events are not enough to train on by themselves.

## 5. Modelling approach
1. **Flood threshold:** **905 m³/s at station 2170** (2015 peak level). Flood = daily peak discharge ≥ 905 m³/s.
   **Target:** daily peak discharge (m³/s), predicted as a **regression**, then compared to the threshold.
2. **Training data:** full daily history (≥ 20 years, ERA5 + gauge), not only the 2 events.
3. **Features:** cumulative precipitation over 1/3/5 days, snow depth change, soil moisture, temperature, previous-day discharge.
4. **Model (discharge regression):**
   - **Baseline:** conceptual model **GR4J + CemaNeige** (snow module by elevation bands; INRAE, standard for French Alpine basins).
   - **Main:** **LSTM** (NeuralHydrology), daily inputs from all points, 365-day lookback to capture snow storage and soil memory.
   - Not tree models (LightGBM/RF) for the peak: they cannot predict above the highest training value, and 2023 was a record.
5. **Forecast probability:** run the model on every member of the **ensemble weather forecast** (Open-Meteo Ensemble API). Probability = the share of members whose predicted discharge is ≥ 905 m³/s (plus the model's own error spread).
6. **Validation:** time-based split (no random shuffle); report the error on high flows (> Q95), the Brier score for ≥ 905 m³/s, and a hindcast of Nov 2023 and 2015.

> **Best practice:**
> - Only 2 days have reached ≥ 905 m³/s since 1904, which is too few to train a classifier. So the model predicts discharge (regression), weighting high flows more, and the probability comes from the threshold.
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
│   ├── store.py          # DuckDB tables built from data/raw/*.parquet
│   ├── fetch_weather.py  # Open-Meteo forecast + archive
│   ├── fetch_discharge.py# Hydroportail / BAFU
│   ├── features.py
│   ├── model.py          # train, calibrate, save
│   └── predict.py        # ensemble forecast -> probability
└── tests/
```

## 8. Not used
- **Reignier-Ésery** gauge (FR): only ~90 min upstream of Étrembières, which is too short a lead time for a D+1–D+7 daily forecast; the station code is unknown and the data is unreliable. It could be added later for short-term (hourly) nowcasting.
