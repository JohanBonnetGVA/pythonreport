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
SITE = "V0220002"        # L'Arve à Reignier-Ésery [Arthaz]
PRIMARY = "V022000201"   # EDF, open 1961: complete daily mean since 1990 → wins on overlap
STATION = "V022000202"   # DREAL, open 2019: only source of daily max (QIXnJ)
SERIES = {"QmnJ": "discharge_mean", "QIXnJ": "discharge_max"}
log = logging.getLogger(__name__)


def site_stations(site: str = SITE) -> list[dict]:
    """All stations (current and closed) of a hydrometric site."""
    r = session().get(f"{BASE}/referentiel/stations", params={"code_site": site, "size": 100}, timeout=60)
    r.raise_for_status()
    return r.json().get("data", [])


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


def fetch(start: date, end: date, code: str = PRIMARY, site: str = SITE) -> pd.DataFrame:
    """Daily mean + max (m³/s), all stations of the site merged.

    `code` wins on overlap (one consistent rating for the long daily-mean series);
    other stations only fill gaps (e.g. daily max from 2019). → data/raw/discharge/<site>_daily.parquet.
    """
    try:
        stations = site_stations(site)
    except Exception as e:  # fall back to the current station only
        log.warning("site lookup failed: %s", e)
        stations = []
    codes = [st["code_station"] for st in stations] or [code]
    for st in stations:
        log.info("site %s station %s: %s, open %s → %s", site, st.get("code_station"), st.get("libelle_station"),
                 st.get("date_ouverture_station"), st.get("date_fermeture_station"))
    codes = sorted(set(codes), key=lambda c: c == code)  # primary last → wins
    merged: dict[str, pd.Series] = {}
    for c in codes:
        parts = {}
        for g, col in SERIES.items():
            try:
                sr = fetch_series(g, start, end, c)
            except ValueError as e:
                log.info("%s", e)
                continue
            parts[col] = sr["value_m3s"]
            parts[f"{col}_qualification"] = sr["qualification"]
        if not parts:
            continue
        d = pd.DataFrame(parts).assign(source_station=c)
        log.info("  %s: %d days %s → %s", c, len(d), d.index.min().date(), d.index.max().date())
        for k in d:
            merged[k] = d[k] if k not in merged else d[k].combine_first(merged[k])
    if not merged:
        raise ValueError(f"Hub'Eau: no data for site {site}")
    d = pd.DataFrame(merged).sort_index()
    out = DATA_RAW / "discharge" / f"{site}_daily.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(out)
    log.info("%s: %d days %s → %s, max daily mean %.0f m³/s on %s", site, len(d), d.index.min().date(),
             d.index.max().date(), d["discharge_mean"].max(), d["discharge_mean"].idxmax().date())
    return d
