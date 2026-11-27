"""Project settings: dates, paths, gauge and weather points."""
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"

START = date(1990, 1, 1)
END = date.today()
TIMEZONE = "Europe/Zurich"

# BAFU gauge, just downstream of Étrembières.
# Target: Reignier-Ésery (Hub'Eau), just upstream of Étrembières. Geneva (BAFU 2170) is
# downstream of the Étrembières flood plain, whose storage attenuates the peak (2023:
# ~1200 m³/s at Reignier vs ~1010 at Geneva), so Geneva under-states the local flood.
TARGET_STATION = "V0220002"   # Hub'Eau site L'Arve à Reignier-Ésery (daily mean since 1990)
DISCHARGE_STATION = "2170"     # BAFU Arve – Genève, Bout du Monde (secondary / cross-check)
FLOOD_EVENT_2015 = ("2015-04-25", "2015-05-10")
HIGH_FLOW_POWER = 2.0  # loss weight (Q/mean Q)^p: makes models fit floods, not average days
TARGET = "discharge_mean"  # daily mean: complete since 1990 (daily max only since 2019)
FLOOD_THRESHOLD_M3S: float | None = None  # None → 2015 peak at TARGET_STATION (717 m³/s daily mean)


def flood_threshold(q_max) -> float:
    """Flood threshold = 2015 daily peak at the target station (unless set explicitly)."""
    if FLOOD_THRESHOLD_M3S is not None:
        return FLOOD_THRESHOLD_M3S
    peak = q_max.loc[FLOOD_EVENT_2015[0]: FLOOD_EVENT_2015[1]].max()
    if peak != peak:  # NaN
        raise ValueError("No 2015 data at target station to set the flood threshold")
    return float(peak)


@dataclass(frozen=True)
class Point:
    name: str
    lat: float
    lon: float
    elevation: int  # m, passed to Open-Meteo for downscaling
    role: str


# Arve catchment points where snow tends to stay (coordinates approx., to verify).
POINTS: tuple[Point, ...] = (
    Point("argentiere", 45.982, 6.927, 1250, "upper Arve, snow"),
    Point("plan_praz", 45.935, 6.851, 2000, "upper Arve, high snow"),
    Point("contamines", 45.821, 6.727, 1160, "Bon Nant, snow"),
    Point("flaine", 46.006, 6.689, 1600, "Grand Massif, snow"),
    Point("samoens_saix", 46.058, 6.712, 1650, "Giffre, snow"),
    Point("praz_de_lys", 46.142, 6.597, 1500, "Giffre/Risse, snow"),
    Point("chinaillon", 45.975, 6.460, 1300, "Borne, snow"),
    Point("etrembieres", 46.178, 6.226, 410, "valley, rain"),
)
