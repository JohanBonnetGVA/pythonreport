"""Hub'Eau hydrometry API (FR, free, no key): Arve at Reignier-Ésery.

Station code from a sentival.fr bulletin, to confirm on hydro.eaufrance.fr (logged at fetch).
Elaborated series: QmnJ = daily mean, QIXnJ = daily max instantaneous. Hub'Eau gives flows in l/s.
Rating curves at high flow are uncertain (Nov 2023: ~1200 m³/s estimated here vs ~1010 at Geneva
downstream), so qualification/status codes are kept.
"""
import logging
from datetime import date

import pandas as pd

from ..config import DATA_RAW
from ._http import session

BASE = "https://hubeau.eaufrance.fr/api/v2/hydrometrie"
STATION = "V022000202"  # L'Arve à Reignier-Ésery
SERIES = {"QmnJ": "discharge_mean", "QIXnJ": "discharge_max"}
log = logging.getLogger(__name__)


def station_info(code: str = STATION) -> dict:
    r = session().get(f"{BASE}/referentiel/stations", params={"code_station": code}, timeout=60)
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        raise ValueError(f"Hub'Eau: unknown station {code}")
    return data[0]


def fetch_series(grandeur: str, start: date, end: date, code: str = STATION) -> pd.DataFrame:
    """All pages of one elaborated series."""
    s, rows = session(), []
    url, params = f"{BASE}/obs_elab", {
        "code_entite": code, "grandeur_hydro_elab": grandeur,
        "date_debut_obs_elab": start.isoformat(), "date_fin_obs_elab": end.isoformat(), "size": 20000,
    }
    while url:
        r = s.get(url, params=params, timeout=120)
        r.raise_for_status()
        j = r.json()
        rows += j.get("data", [])
        url, params = j.get("next"), None  # 'next' already carries the query
    if not rows:
        raise ValueError(f"Hub'Eau: no {grandeur} data for {code}")
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "value_m3s": df["resultat_obs_elab"].astype(float) / 1000,  # l/s → m³/s
        "qualification": df.get("libelle_qualification"),
        "status": df.get("libelle_statut"),
    })
    out.index = pd.to_datetime(df["date_obs_elab"]).dt.tz_localize(None).dt.normalize().rename("time")
    return out.sort_index()


def fetch(start: date, end: date, code: str = STATION) -> pd.DataFrame:
    """Daily mean + max (m³/s), same schema as BAFU daily → data/raw/discharge/<code>_daily.parquet."""
    try:
        info = station_info(code)
        log.info("Hub'Eau station %s: %s (%s)", code, info.get("libelle_station"), info.get("en_service"))
    except Exception as e:  # informational only
        log.warning("station lookup failed: %s", e)
    parts = {}
    for g, col in SERIES.items():
        sr = fetch_series(g, start, end, code)
        parts[col] = sr["value_m3s"]
        parts[f"{col}_qualification"] = sr["qualification"]
    d = pd.DataFrame(parts)
    out = DATA_RAW / "discharge" / f"{code}_daily.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(out)
    log.info("%s: %d days %s → %s, max %.0f m³/s on %s", code, len(d), d.index.min().date(),
             d.index.max().date(), d["discharge_max"].max(), d["discharge_max"].idxmax().date())
    return d
