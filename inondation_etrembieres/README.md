# Inondation Étrembières

Flood probability for the Arve at Étrembières (Haute-Savoie): GR4J+snow model, NOAA GEFS ensemble hindcasts,
σ flood-condition indicators and two alerts (hard / forecast). Design: [SPEC.md](SPEC.md). Current state: [CLAUDE.md](CLAUDE.md).

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # optional extras: gefs, lstm
pytest -q
inondation-fetch db          # build DuckDB from committed data
inondation-fetch models      # model comparison
inondation-fetch alerts      # hard + forecast alerts (needs data/raw/gefs)
```

Data sources: Open-Meteo (ERA5), Hub'Eau (Reignier-Ésery), NOAA GEFS (AWS open data), BAFU (Geneva, credit FOEN/BAFU).
