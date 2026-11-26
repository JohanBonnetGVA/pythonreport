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
DISCHARGE_STATION = "2170"  # Arve – Genève, Bout du Monde
FLOOD_THRESHOLD_M3S = 905.0  # 2015 peak


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
