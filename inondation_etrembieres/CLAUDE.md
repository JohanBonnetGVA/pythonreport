# Inondation Étrembières — working notes for Claude

User preferences: keep answers short, show only changes (snippets), flag best-practice choices.
Read `SPEC.md` for the full design. This file is the current state plus the next steps.

## Setup
```bash
cd inondation_etrembieres
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"        # extras: gefs (pygrib), lstm (torch, CPU: --index-url https://download.pytorch.org/whl/cpu)
pytest -q                      # 17 pass, LSTM test skipped without torch
inondation-fetch db            # build data/processed/inondation.duckdb from committed parquet
```

## Data (committed under data/raw/, .gitignore excludes data/** except via `git add -f`)
- `weather/<point>.parquet`: Open-Meteo archive, model **era5_seamless** (ERA5-Land snow, soil, temperature + ERA5 precipitation; ERA5-Land alone has no precipitation on Open-Meteo). 1990→ daily, 8 points (`config.POINTS`). `chunks/` holds the 2-year resume cache.
- `discharge/V0220002_daily.parquet`: **target**, Hub'Eau site Reignier-Ésery. Daily mean 1990→ (EDF V022000201, wins on overlap), daily max 2019→ (DREAL V022000202).
- `gefs/<YYYYMMDD>.parquet`: NOAA GEFS past ensembles, 16 lead days, inits D-16..D-1 before both floods (2015: reforecast, 5–11 members; 2023: operational, 31 members).
- Geneva BAFU 2170 is a cross-check only (no free download; the Étrembières flood plain lowers its peak).

## Key facts
- Floods: 2 May 2015 (717 m³/s daily mean at Reignier), 15 Nov 2023 (827 mean, 1234 daily max). Next highest since 1990: 454.
- Flood threshold = 2015 peak (`config.flood_threshold`), 717 m³/s daily mean.
- Split: train 1990–2016, val 2017–21, test 2022–23 (`manager.Split`).

## Modules (src/inondation_etrembieres/)
| File | Role |
|---|---|
| `sources/weather.py`, `hubeau.py`, `discharge.py`, `gefs.py` | one fetcher per source; 429-aware, resumable, fail loudly on all-null variables |
| `store.py` | DuckDB tables `weather_daily`, `discharge_daily` built from the parquet files |
| `dataset.py` | `Dataset(weather MultiIndex(point,var), y=daily mean, qmax)` |
| `models/` | `persistence`, `ridge`, `gr4j_snow` (numba GR4J + degree-day snow, 6 params), `lstm`; high-flow weighted objectives (`config.HIGH_FLOW_POWER`) |
| `manager.py` | trains and compares all models; 2015/2023 peaks; model-relative thresholds (2015-relative, quantile-mapped) |
| `peak.py` | daily max = mean × exp(a + b·log mean + c·log rise), fitted 2019–2022 |
| `conditions.py` | σ indicators (rain_3d, wetness API k=0.9, snow, warming, rain_on_snow ≥ 0.2 m snow); levels watch/warning/alert at rain 2/4/5 σ |
| `hindcast.py` | GEFS member → ERA5 state up to init−1 + forecast → GR4J; `run()`, `run_conditions()`, `--temp era5` experiment |
| `alerts.py` | HARD (observed conditions + flow), FORECAST (GEFS conditions + GR4J), OUTLOOK (all-clear days 1–7 only, days 8–16 trend) |
| `cli.py` | `inondation-fetch {weather,hubeau,discharge,db,models,gefs,hindcast,conditions,alerts}` |

## Results so far
- GR4J+snow is the best model (test NSE 0.75). Peaks are ~33 % low; daily max via peak factor ~25 % low. Its matched threshold (386 m³/s) catches both floods with 2 false-alarm days in 2022–23. The LSTM overfits 2015.
- Forecast alert: red 4–5 days before both floods. The day-before GR4J probability for 2023 was 68 %.
- GEFS days 8–16 showed nothing before either flood, so no all-clear beyond day 7. With that rule: 0 wrong all-clears.
- Hard alert red: 4 episodes since 1990, all real high flows (same day or the day before the peak).
- Official post-event analysis of 2023: rain/snow limit at 2,800–3,000 m, uncertain observed rain, 3–4 h warning at Sallanches, a young SPC setup (2020). Hourly modelling would help.

## CI (.github/workflows/)
- `fetch-data.yml`: weather + Hub'Eau, commits data. Weather is incremental.
- `models.yml`: model comparison table in the run summary.
- `hindcast.yml`: GEFS fetch (cached), hindcast, conditions, alerts.
- Workflows commit data with `[skip ci]`; push uses `pull --rebase`. Scheduled triggers only work on the default branch.

## Next steps (agreed direction)
1. **Live mode** `inondation-fetch live` → `status.json` (`hard`, `forecast`, `outlook`, `final`, `drivers`, `updated`). Sources: Open-Meteo forecast (`past_days` for the ERA5 lag) and ensemble API (GEFS/ECMWF members), Hub'Eau real-time flow.
2. **Home Assistant**: rest sensor on status.json, notify on level change, stale warning if older than 30 h. Run on the HA machine (Docker/cron, preferred) or as a scheduled GitHub Action (needs a merge to master).
3. False-alarm check on non-flood weeks with the GEFS reforecast 2000–2019.
4. Weather-pattern layer from GEFS (sea-level pressure anticyclone index, southwesterly flow, 0 °C level).
5. Optional: hourly modelling (Hydroportail export), a rain-on-snow melt term, confirming AREA_KM2 = 1664 for Reignier.
