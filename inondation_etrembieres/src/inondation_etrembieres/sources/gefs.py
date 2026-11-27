"""NOAA GEFS past ensemble forecasts (public AWS buckets, no account) at the project points.

- 2000–2019: GEFSv12 **reforecast** (bucket noaa-gefs-retrospective), 00Z, 5 members
  (c00, p01–p04), one GRIB2 file per variable/member with all lead times of days 1–10.
- 2017→: **operational** archive (bucket noaa-gefs-pds), 00Z, 31 members (gec00, gep01–30),
  one GRIB2 file per lead time with a .idx → only the needed fields are byte-range fetched.

Lead days 1–10. Output per init date: data/raw/gefs/<YYYYMMDD>.parquet, long format
(member, date, point, precip_mm, temp_c, z_grid). Days are UTC (00Z init → day 1 = 00–24 UTC).
"""
import logging
import re
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import DATA_RAW, POINTS
from ._http import session

log = logging.getLogger(__name__)
REFORECAST = "https://noaa-gefs-retrospective.s3.amazonaws.com"
OPERATIONAL = "https://noaa-gefs-pds.s3.amazonaws.com"
LEAD_DAYS = 16  # GEFS horizon: reforecast "Days:1-10" + "Days:10-16" files, operational f000–f384
OUT = DATA_RAW / "gefs"
_local = threading.local()


def _s():
    if not hasattr(_local, "s"):
        _local.s = session()
    return _local.s


def list_keys(base: str, prefix: str) -> list[str]:
    """S3 ListObjectsV2 over anonymous HTTPS (handles pagination)."""
    keys, token = [], None
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    while True:
        params = {"list-type": 2, "prefix": prefix, **({"continuation-token": token} if token else {})}
        r = _s().get(base, params=params, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        keys += [e.text for e in root.findall("s3:Contents/s3:Key", ns)]
        token_el = root.find("s3:NextContinuationToken", ns)
        if token_el is None:
            return keys
        token = token_el.text


# ---------------------------------------------------------------- GRIB helpers
def _nearest_idx(lats: np.ndarray, lons: np.ndarray) -> list[tuple[int, int]]:
    """Grid indices of the nearest cell to each project point (lon in 0–360)."""
    out = []
    for p in POINTS:
        d = (lats - p.lat) ** 2 + ((lons % 360) - (p.lon % 360)) ** 2
        out.append(np.unravel_index(np.argmin(d), d.shape))
    return out


def _records(msgs, member: str, init: date) -> list[dict]:
    """GRIB messages → rows (member, var, start_h, end_h, point, value)."""
    rows, idx = [], None
    for m in msgs:
        if idx is None:
            idx = _nearest_idx(*m.latlons())
        name = m["shortName"]
        var = {"tp": "precip", "2t": "temp", "t": "temp", "orog": "z", "gh": "z"}.get(name, name)
        vals = m.values
        for p, (i, j) in zip(POINTS, idx):
            rows.append({"member": member, "var": var, "start_h": int(m["startStep"]),
                         "end_h": int(m["endStep"]), "point": p.name, "value": float(vals[i, j])})
    return rows


def _to_daily(df: pd.DataFrame, init: date) -> pd.DataFrame:
    """3/6-hourly records → daily precip (mm) and mean temperature (°C) per member/point."""
    out = []
    for (member, point), g in df.groupby(["member", "point"]):
        pr = g[g["var"] == "precip"].sort_values(["start_h", "end_h"])
        # accumulations: within a common start, successive differences; groups tile the time axis
        inc = []
        for _, gs in pr.groupby("start_h"):
            v = gs["value"].to_numpy()
            inc += list(zip(gs["end_h"].to_numpy(), np.diff(np.r_[0.0, v]).clip(min=0)))
        p = pd.DataFrame(inc, columns=["end_h", "mm"]).drop_duplicates("end_h")
        p["day"] = (p["end_h"] - 1) // 24 + 1
        t = g[g["var"] == "temp"].assign(day=lambda x: (x["end_h"] - 1).clip(lower=0) // 24 + 1)
        z = g.loc[g["var"] == "z", "value"]
        d = pd.DataFrame({
            "precip_mm": p.groupby("day")["mm"].sum(),
            "temp_c": t.groupby("day")["value"].mean() - 273.15,
        })
        d = d[(d.index >= 1) & (d.index <= LEAD_DAYS)]
        d["date"] = [pd.Timestamp(init) + timedelta(days=int(k) - 1) for k in d.index]
        d["member"], d["point"], d["z_grid"] = member, point, (z.iloc[0] if len(z) else np.nan)
        out.append(d.reset_index(drop=True))
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- reforecast (≤2019)
def fetch_reforecast(init: date) -> pd.DataFrame:
    import pygrib

    prefix = f"GEFSv12/reforecast/{init:%Y}/{init:%Y%m%d}00/"
    keys = [k for k in list_keys(REFORECAST, prefix) if "Days:1-10" in k or (LEAD_DAYS > 10 and "Days:10-16" in k)]
    want = [k for k in keys if re.search(r"/(apcp_sfc|tmp_2m|hgt_sfc)_", k)
            and not ("Days:10-16" in k and "hgt_sfc" in k)]  # orography once is enough
    if not want:
        raise FileNotFoundError(f"No reforecast files under {prefix} (found {len(keys)} keys: {keys[:5]})")
    log.info("reforecast %s: %d files", init, len(want))

    def one(key: str) -> list[dict]:
        member = key.split("/")[4]
        with tempfile.NamedTemporaryFile(suffix=".grib2") as f:
            with _s().get(f"{REFORECAST}/{key}", stream=True, timeout=600) as r:
                r.raise_for_status()
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            f.flush()
            g = pygrib.open(f.name)
            rows = _records(g, member, init)
            g.close()
        return rows

    with ThreadPoolExecutor(4) as ex:
        rows = [r for rs in ex.map(one, want) for r in rs]
    df = pd.DataFrame(rows).drop_duplicates(["member", "var", "start_h", "end_h", "point"])  # day-10 overlap
    # orography is member-independent in the reforecast: broadcast to all members
    z = df[df["var"] == "z"].drop(columns="member").drop_duplicates("point")
    df = df[df["var"] != "z"]
    df = pd.concat([df] + [z.assign(member=m) for m in df["member"].unique()], ignore_index=True)
    return _to_daily(df, init)


# ---------------------------------------------------------------- operational (≥2017)
FIELDS = (r":APCP:surface:", r":TMP:2 m above ground:", r":HGT:surface:")


def _ranges(idx_text: str) -> list[tuple[int, int | None]]:
    lines = [l for l in idx_text.strip().splitlines() if l]
    offs = [int(l.split(":")[1]) for l in lines]
    out = []
    for k, l in enumerate(lines):
        if any(re.search(f, l) for f in FIELDS):
            out.append((offs[k], offs[k + 1] - 1 if k + 1 < len(offs) else None))
    return out


def fetch_operational(init: date, members: int = 30) -> pd.DataFrame:
    import pygrib

    root = f"gefs.{init:%Y%m%d}/00/atmos/pgrb2ap5/"
    names = ["gec00"] + [f"gep{i:02d}" for i in range(1, members + 1)]
    probe = list_keys(OPERATIONAL, root + "gec00.t00z.pgrb2a.0p50.f006")
    if not probe:
        raise FileNotFoundError(f"No operational files under {root}: {list_keys(OPERATIONAL, f'gefs.{init:%Y%m%d}/00/')[:10]}")
    jobs = [(m, h) for m in names for h in [0] + list(range(6, 24 * LEAD_DAYS + 1, 6))]
    log.info("operational %s: %d members × %d leads", init, len(names), len(jobs) // len(names))

    def one(job) -> list[dict]:
        member, h = job
        url = f"{OPERATIONAL}/{root}{member}.t00z.pgrb2a.0p50.f{h:03d}"
        r = _s().get(url + ".idx", timeout=60)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        rows = []
        for a, b in _ranges(r.text):
            rr = _s().get(url, headers={"Range": f"bytes={a}-{'' if b is None else b}"}, timeout=120)
            rr.raise_for_status()
            rows += _records([pygrib.fromstring(rr.content)], member, init)
        return rows

    with ThreadPoolExecutor(32) as ex:
        rows = [r for rs in ex.map(one, jobs) for r in rs]
    return _to_daily(pd.DataFrame(rows), init)


def fetch(init: date) -> pd.DataFrame:
    """Daily ensemble forecast for one 00Z init date, cached to data/raw/gefs/<date>.parquet."""
    out = OUT / f"{init:%Y%m%d}.parquet"
    if out.exists():
        cached = pd.read_parquet(out)
        if cached["date"].nunique() >= LEAD_DAYS or cached.get("source_max_days") is not None:
            # full horizon, or already the longest the source offers (flag set at fetch)
            return cached
        log.info("  %s: cache has %d days < %d, re-fetching", init, cached["date"].nunique(), LEAD_DAYS)
    d = fetch_reforecast(init) if init.year <= 2019 else fetch_operational(init)
    if d["date"].nunique() < LEAD_DAYS:  # source shorter than requested: don't re-fetch forever
        log.warning("  %s: source has only %d of %d lead days", init, d["date"].nunique(), LEAD_DAYS)
        d["source_max_days"] = d["date"].nunique()
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(out)
    log.info("  %s: %d members, %d rows, precip day1–%d mean %.1f mm", init, d["member"].nunique(), len(d),
             LEAD_DAYS, d.groupby(["member", "point"])["precip_mm"].sum().mean())
    return d


def init_dates(peak: str, days_before: int = LEAD_DAYS) -> list[date]:
    p = pd.Timestamp(peak).date()
    return [p - timedelta(days=k) for k in range(days_before, 0, -1)]
