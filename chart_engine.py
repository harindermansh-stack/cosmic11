"""
chart_engine.py — deterministic sidereal Vedic chart calculation.

This is the piece CLAUDE.md confirms was NEVER wired into app.py:
"ephe/ ... not currently wired into app.py". Right now app.py's Ascendant/
Moon/Dasha/Nakshatra are hand-picked from sidebar dropdowns, not computed —
that's a big part of why answers feel generic/shallow: there's no house-by-
house planetary picture behind the scenes at all, only 4 top-level facts.

Uses Lahiri ayanamsa (standard for Vedic/Parashari astrology) and whole-sign
houses (the classical Parashari house system BPHS itself uses).

Requires: pip install pyswisseph
Requires: the ephe/ folder (already present in your repo) OR falls back to
Swiss Ephemeris's built-in Moshier approximation if ephe/ isn't found —
Moshier is accurate to within about 1 arcminute for modern dates, which is
fine for testing but you should point EPHE_PATH at your real ephe/ folder
for production-grade precision.
"""

import os
import swisseph as swe
from dataclasses import dataclass
from datetime import datetime
from typing import List

# ---- point this at your repo's real ephe/ folder ----
EPHE_PATH = os.getenv("EPHE_PATH") or r"C:\VedicRAG\ephe"
if not os.path.isdir(EPHE_PATH):                      # e.g. on the Linux server: use the ephemeris files from the
    try:                                              # xingpan-ephemeris package (same Swiss Ephemeris data files)
        import xingpan_ephemeris
        EPHE_PATH = os.path.join(os.path.dirname(xingpan_ephemeris.__file__), "data")
    except ImportError:
        pass
try:
    swe.set_ephe_path(EPHE_PATH)
except Exception:
    pass  # falls back to Moshier approximation if the path/files aren't found



def _init_swe():
    """Swiss Ephemeris keeps its settings PER THREAD. Streamlit and FastAPI run
    calculations in worker threads, so a one-time setting at import silently
    falls back to the default (Fagan-Bradley) there, about 0.9 degrees off from
    Lahiri. Apply the settings inside every calculation instead."""
    try:
        swe.set_ephe_path(EPHE_PATH)
    except Exception:
        pass
    swe.set_sid_mode(swe.SIDM_LAHIRI)


SIGNS = ["Aries","Taurus","Gemini","Cancer","Leo","Virgo","Libra","Scorpio",
         "Sagittarius","Capricorn","Aquarius","Pisces"]

NAKSHATRAS = ["Ashwini","Bharani","Krittika","Rohini","Mrigashira","Ardra",
              "Punarvasu","Pushya","Ashlesha","Magha","Purva Phalguni",
              "Uttara Phalguni","Hasta","Chitra","Swati","Vishakha","Anuradha",
              "Jyeshtha","Mula","Purva Ashadha","Uttara Ashadha","Shravana",
              "Dhanishtha","Shatabhisha","Purva Bhadrapada","Uttara Bhadrapada",
              "Revati"]

# Vimshottari dasha sequence + years, starting from Ketu (fixed classical order)
DASHA_SEQUENCE = [("Ketu",7),("Venus",20),("Sun",6),("Moon",10),("Mars",7),
                   ("Rahu",18),("Jupiter",16),("Saturn",19),("Mercury",17)]
DASHA_YEARS_TOTAL = sum(y for _, y in DASHA_SEQUENCE)  # 120

PLANET_IDS = {
    "Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS, "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER, "Venus": swe.VENUS, "Saturn": swe.SATURN,
}


@dataclass
class PlanetPlacement:
    planet: str
    longitude: float      # sidereal, 0-360
    sign: str
    sign_lord_deg: float  # degrees into the sign, 0-30
    house: int             # whole-sign house from ascendant, 1-12
    nakshatra: str
    pada: int
    retrograde: bool = False


@dataclass
class ChartResult:
    ascendant_sign: str
    ascendant_deg: float
    ascendant_nakshatra: str
    placements: List[PlanetPlacement]
    active_mahadasha: str
    mahadasha_start: datetime
    mahadasha_end: datetime


def _sign_of(longitude: float) -> str:
    return SIGNS[int(longitude // 30) % 12]


def _nakshatra_of(longitude: float):
    span = 360 / 27  # 13°20'
    idx = int(longitude // span) % 27
    pos_in_nak = longitude % span
    pada = int(pos_in_nak // (span / 4)) + 1
    return NAKSHATRAS[idx], pada


def _julian_day_ut(dt_local: datetime, utc_offset_hours: float) -> float:
    dt_utc_hour = dt_local.hour + dt_local.minute/60 + dt_local.second/3600 - utc_offset_hours
    return swe.julday(dt_local.year, dt_local.month, dt_local.day, dt_utc_hour)


def calculate_chart(birth_datetime: datetime, lat: float, lon: float,
                     utc_offset_hours: float = 5.5) -> ChartResult:
    """
    birth_datetime: naive local datetime (year, month, day, hour, minute)
    lat/lon: birthplace, e.g. 28.8955, 76.6066
    utc_offset_hours: 5.5 for India (IST)
    """
    _init_swe()  # must run in the SAME thread as the calculations below
    jd = _julian_day_ut(birth_datetime, utc_offset_hours)
    flags = swe.FLG_SWIEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED

    # Ascendant (sidereal): compute tropical houses, then subtract ayanamsa
    cusps, ascmc = swe.houses_ex(jd, lat, lon, b'W')  # whole-sign house system
    ayanamsa = swe.get_ayanamsa_ut(jd)
    asc_sidereal = (ascmc[0] - ayanamsa) % 360
    asc_sign = _sign_of(asc_sidereal)
    asc_sign_index = SIGNS.index(asc_sign)
    asc_nak, _ = _nakshatra_of(asc_sidereal)

    placements = []
    for name, pid in PLANET_IDS.items():
        (lon_deg, lat_deg, dist, speed_lon, *_) = swe.calc_ut(jd, pid, flags)[0]
        sign = _sign_of(lon_deg)
        sign_index = SIGNS.index(sign)
        house = ((sign_index - asc_sign_index) % 12) + 1
        nak, pada = _nakshatra_of(lon_deg)
        placements.append(PlanetPlacement(
            planet=name, longitude=lon_deg, sign=sign,
            sign_lord_deg=lon_deg % 30, house=house,
            nakshatra=nak, pada=pada, retrograde=(speed_lon < 0),
        ))

    # Rahu/Ketu (mean node)
    (rahu_lon, *_) = swe.calc_ut(jd, swe.MEAN_NODE, flags)[0]
    ketu_lon = (rahu_lon + 180) % 360
    for name, lon_deg in [("Rahu", rahu_lon), ("Ketu", ketu_lon)]:
        sign = _sign_of(lon_deg)
        sign_index = SIGNS.index(sign)
        house = ((sign_index - asc_sign_index) % 12) + 1
        nak, pada = _nakshatra_of(lon_deg)
        placements.append(PlanetPlacement(
            planet=name, longitude=lon_deg, sign=sign,
            sign_lord_deg=lon_deg % 30, house=house,
            nakshatra=nak, pada=pada, retrograde=True,  # nodes are always "retrograde"
        ))

    # ---- Vimshottari Mahadasha from Moon's nakshatra ----
    moon = next(p for p in placements if p.planet == "Moon")
    span = 360 / 27
    nak_idx = NAKSHATRAS.index(moon.nakshatra)
    portion_elapsed = (moon.longitude % span) / span      # 0.0-1.0 through the nakshatra
    start_lord_idx = nak_idx % 9                            # cycles through the 9 dasha lords

    # Balance of the FIRST (birth) dasha, in years
    first_lord, first_years = DASHA_SEQUENCE[start_lord_idx]
    balance_years = first_years * (1 - portion_elapsed)

    # Walk forward from birth through dasha periods until we bracket "today"
    from datetime import timedelta
    cursor = birth_datetime
    lord_idx = start_lord_idx
    years_remaining = balance_years
    today = datetime.now()

    period_start = birth_datetime
    period_end = birth_datetime + timedelta(days=years_remaining * 365.25)
    while period_end < today:
        lord_idx = (lord_idx + 1) % 9
        _, yrs = DASHA_SEQUENCE[lord_idx]
        period_start = period_end
        period_end = period_start + timedelta(days=yrs * 365.25)

    active_lord = DASHA_SEQUENCE[lord_idx][0] if period_start != birth_datetime else first_lord

    return ChartResult(
        ascendant_sign=asc_sign, ascendant_deg=asc_sidereal % 30,
        ascendant_nakshatra=asc_nak, placements=placements,
        active_mahadasha=active_lord,
        mahadasha_start=period_start, mahadasha_end=period_end,
    )


if __name__ == "__main__":
    # Validation run against YOUR OWN already-confirmed chart:
    # Libra Asc 23°46', Moon Karka/Cancer ~101°26' sidereal (Pushya, pada 3),
    # Mars exalted in Capricorn, Venus+Jupiter in Libra, Sun+Mercury in Scorpio,
    # Saturn retrograde in Aries, Rahu in Aquarius, Ketu in Leo.
    chart = calculate_chart(
        birth_datetime=datetime(1969, 11, 29, 5, 30),
        lat=28.8955, lon=76.6066, utc_offset_hours=5.5,
    )
    print(f"Ascendant: {chart.ascendant_sign} {chart.ascendant_deg:.2f} deg ({chart.ascendant_nakshatra})")
    print(f"Active Mahadasha: {chart.active_mahadasha}  ({chart.mahadasha_start.date()} -> {chart.mahadasha_end.date()})")
    print("\nPlanet   Sign          Deg     House  Nakshatra          Pada  Retro")
    for p in chart.placements:
        print(f"{p.planet:<8} {p.sign:<13} {p.sign_lord_deg:5.2f}   {p.house:<5}  {p.nakshatra:<18} {p.pada:<5} {p.retrograde}")
