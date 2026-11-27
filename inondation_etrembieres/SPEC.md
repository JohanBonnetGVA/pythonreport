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
| River discharge (m³/s) | **target** | Hub'Eau site **V0220002 Arve à Reignier-Ésery** (FR, just upstream of Étrembières): daily mean 1990→ (EDF station V022000201), daily max 2019→ (DREAL V022000202) |
| River discharge (m³/s) | cross-check | BAFU **2170 Arve – Genève Bout du Monde** (CH, since 1904, downstream; peak attenuated by the Étrembières flood plain) |

Catchment variables are taken as a **basin average** over the upper Arve (Chamonix → Étrembières), not just at the town.

## 4. Reference events
| Event | Peak Geneva (2170) | Notes |
|---|---|---|
| 15 Nov 2023 | ~1010 m³/s (record since 1904) | Successive storms on saturated soils; ~1200 m³/s est. at Reignier-Ésery |
| End Apr – 2 May 2015 | ~905 m³/s (2 May) | Heavy rain from end of April + snowmelt; lower than 2023 |

These events are used to **validate** the model (did it predict a high probability?). Two events are not enough to train on by themselves.

## 5. Modelling approach
1. **Flood threshold:** **717 m³/s daily mean at Reignier-Ésery** (2 May 2015; Geneva equivalent ~905 m³/s instantaneous). Flood = daily mean ≥ 717 m³/s. Only 2015 (717) and 2023 (743) reach it since 1990; next highest is 2022 (454).
   **Target:** daily mean discharge (complete since 1990; daily max only since 2019), predicted as a **regression**, then compared to the threshold.
   **Why Reignier:** the Étrembières flood plain stores water and lowers the peak at Geneva (2023: ~1200 vs ~1010 m³/s), so Geneva under-states the local flood.
2. **Training data:** full daily history (≥ 20 years, ERA5 + gauge), not only the 2 events.
3. **Features:** cumulative precipitation over 1/3/5 days, snow depth change, soil moisture, temperature, previous-day discharge.
4. **Model (discharge regression):**
   - **Baseline:** conceptual model **GR4J + CemaNeige** (snow module by elevation bands; INRAE, standard for French Alpine basins).
   - **Main:** **LSTM** (NeuralHydrology), daily inputs from all points, 365-day lookback to capture snow storage and soil memory.
   - Not tree models (LightGBM/RF) for the peak: they cannot predict above the highest training value, and 2023 was a record.
5. **Forecast probability:** run the model on every member of the **ensemble weather forecast** (Open-Meteo Ensemble API). Probability = the share of members whose predicted discharge is ≥ the 2015 threshold (plus the model's own error spread).
6. **Hindcast (forecast skill):** NOAA GEFS past ensembles (public AWS: reforecast 2000–2019, 5 members; operational archive 2017→, 31 members). For inits 1–7 days before each peak: ERA5 history up to init−1 + GEFS days 1–7 → GR4J → P(flood) = share of members above the model's quantile-mapped threshold (`hindcast.py`).
7. **Validation:** time-based split (no random shuffle); report the error on high flows (> Q95), the Brier score for ≥ threshold, and a hindcast of Nov 2023 and 2015.

> **Best practice:**
> - Only 2 events reach the 2015 level in the record, which is too few to train a classifier. So the model predicts discharge (regression), weighting high flows more, and the probability comes from the threshold.
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
