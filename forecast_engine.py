"""
forecast_engine.py  -  verified timeline facts for ANY period, from birth data alone.

What it computes (Swiss Ephemeris, sidereal Lahiri):
  * Vimshottari Mahadasha / Antardasha (and Pratyantardasha for windows up to about a year)
  * Jupiter, Saturn, Rahu and Ketu sign changes (and Jupiter/Saturn retrograde stations),
    each with the house counted from the natal Moon and from the Lagna
  * Sade Sati spans (Saturn over the 12th, 1st and 2nd sign from the natal Moon)

It does NOT interpret anything. Interpretation must come from the retrieved book passages.
The period is read from the question ("2028", "2027 to 2035", "next 10 years", "March 2029",
"this year", ...). No period is hard-coded.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import swisseph as swe
from plain_layer import QA_STYLE, QA_FORECAST_RULES, plain_facts_text, must_mention

from chart_engine import _init_swe, _julian_day_ut  # same settings + same Julian-day rule as the chart

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio",
         "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
DASHA_SEQ = [("Ketu", 7), ("Venus", 20), ("Sun", 6), ("Moon", 10), ("Mars", 7),
             ("Rahu", 18), ("Jupiter", 16), ("Saturn", 19), ("Mercury", 17)]
YEAR = 365.25                      # days per dasha year (same convention as chart_engine)
NAK_SPAN = 360.0 / 27
FLAGS = swe.FLG_SWIEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
PLANET_IDS = {"Jupiter": swe.JUPITER, "Saturn": swe.SATURN, "Rahu": swe.MEAN_NODE,
              "Sun": swe.SUN, "Mars": swe.MARS, "Mercury": swe.MERCURY, "Venus": swe.VENUS}
FAST = ("Sun", "Mars", "Mercury", "Venus")          # tracked only for periods up to about a year (month-wise reports)
STATION_PLANETS = ("Jupiter", "Saturn", "Mars", "Mercury", "Venus")
# Transit table (houses counted from the natal Moon) for the two slow planets; see Phala Deepika transit chapter.
FAVOURABLE = {"Jupiter": [2, 5, 7, 9, 11], "Saturn": [3, 6, 11], "Sun": [3, 6, 10, 11], "Mars": [3, 6, 10, 11],
              "Mercury": [2, 4, 6, 8, 10, 11], "Venus": [1, 2, 3, 4, 5, 8, 9, 11, 12]}
MAX_YEARS = 60


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ------------------------------------------------------------------ period parsing
@dataclass
class Window:
    start: date
    end: date
    label: str
    note: str = ""


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
           "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}
_YEAR = r"((?:19|20|21)\d{2})"
_INTENT = re.compile(
    r"\b(predict\w*|forecast\w*|future|upcoming|coming\s+(?:year|years|month|months|period|time)|"
    r"next\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve|year|month|decade|few)|"
    r"this\s+year|timeline|dasha|antardasha|bhukti|transit\w*|gochar\w*|sade\s*sati|years?\s+ahead)\b")


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    day = min(d.day, [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date(y, m, day)


def _month_end(y: int, m: int) -> date:
    return _add_months(date(y, m, 1), 1) - timedelta(days=1)


def is_forecast_question(text: str, birth_year: int | None = None) -> bool:
    t = text.lower()
    if _INTENT.search(t):
        return True
    return any(int(y) != birth_year for y in re.findall(r"\b" + _YEAR + r"\b", t))


def parse_period(text: str, today: date | None = None, birth_year: int | None = None) -> Window | None:
    """Read the period a question is about. Returns None if the question is not about a period."""
    today = today or date.today()
    t = " " + text.lower() + " "

    m = re.search(r"\b" + _YEAR + r"\s*(?:to|-|\u2013|\u2014|till|until|through|and)\s*" + _YEAR + r"\b", t)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return _cap(Window(date(a, 1, 1), date(b, 12, 31), f"{a} to {b}"))

    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+" + _YEAR + r"\b", t)
    if m and int(m.group(2)) != birth_year:
        mo, y = _MONTHS[m.group(1)], int(m.group(2))
        return Window(date(y, mo, 1), _month_end(y, mo), f"{m.group(1).capitalize()} {y}")

    m = re.search(r"\b(?:next|coming|upcoming|following)\s+(\d+|" + "|".join(_NUMWORDS) + r")\s*(year|month|week|day)s?\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _NUMWORDS[m.group(1)]
        unit = m.group(2)
        end = {"year": lambda: _add_years(today, n), "month": lambda: _add_months(today, n),
               "week": lambda: today + timedelta(weeks=n), "day": lambda: today + timedelta(days=n)}[unit]()
        return _cap(Window(today, end, f"next {n} {unit}{'s' if n != 1 else ''}"))

    if re.search(r"\b(?:next|coming|upcoming)\s+decade\b", t):
        return Window(today, _add_years(today, 10), "next 10 years")
    if re.search(r"\bnext\s+year\b", t):
        return Window(date(today.year + 1, 1, 1), date(today.year + 1, 12, 31), str(today.year + 1))
    if re.search(r"\bthis\s+year\b", t):
        return Window(today, date(today.year, 12, 31), f"rest of {today.year}")
    if re.search(r"\bnext\s+month\b", t):
        first = _add_months(date(today.year, today.month, 1), 1)
        return Window(first, _month_end(first.year, first.month), first.strftime("%B %Y"))
    if re.search(r"\bthis\s+month\b", t):
        return Window(today, _month_end(today.year, today.month), today.strftime("%B %Y"))

    years = [int(y) for y in re.findall(r"\b" + _YEAR + r"\b", t) if int(y) != birth_year]
    if years:
        a, b = min(years), max(years)
        return _cap(Window(date(a, 1, 1), date(b, 12, 31), str(a) if a == b else f"{a} to {b}"))

    if is_forecast_question(text, birth_year):
        return Window(today, _add_years(today, 1), "next 12 months",
                      "No period was named, so the next 12 months are shown.")
    return None


def _cap(w: Window) -> Window:
    limit = _add_years(w.start, MAX_YEARS)
    if w.end > limit:
        w.end = limit
        w.note = f"Period limited to {MAX_YEARS} years."
    return w


# ------------------------------------------------------------------ ephemeris scanning
def _lon_speed(planet: str, jd: float):
    if planet == "Ketu":
        lon, sp = _lon_speed("Rahu", jd)
        return (lon + 180.0) % 360.0, sp
    r = swe.calc_ut(jd, PLANET_IDS[planet], FLAGS)[0]
    return r[0] % 360.0, r[3]


def _bisect(is_old, lo: float, hi: float, iters: int = 32) -> float:
    for _ in range(iters):
        mid = (lo + hi) / 2
        if is_old(mid):
            lo = mid
        else:
            hi = mid
    return hi


def _scan(planet: str, jd0: float, jd1: float, step: float = 1.0):
    """Sign segments and retrograde/direct stations for one planet between two Julian days."""
    lon, sp = _lon_speed(planet, jd0)
    sign, retro = int(lon // 30), sp < 0
    seg_start, segments, stations = jd0, [], []
    jd = jd0
    while jd < jd1:
        nxt = min(jd + step, jd1)
        lon, sp = _lon_speed(planet, nxt)
        s, r = int(lon // 30), sp < 0
        events = []
        if s != sign:
            t = _bisect(lambda x: int(_lon_speed(planet, x)[0] // 30) == sign, jd, nxt)
            events.append((t, "sign", s))
        if r != retro and planet in STATION_PLANETS:
            t = _bisect(lambda x: (_lon_speed(planet, x)[1] < 0) == retro, jd, nxt)
            events.append((t, "station", r))
        for t, kind, val in sorted(events):
            if kind == "sign":
                segments.append((seg_start, t, sign))
                seg_start, sign = t, val
            else:
                stations.append((t, "retrograde" if val else "direct", sign))
        retro = r
        jd = nxt
    segments.append((seg_start, jd1, sign))
    return segments, stations


def _clip(segments, lo, hi):
    out = []
    for a, b, s in segments:
        a2, b2 = max(a, lo), min(b, hi)
        if b2 > a2:
            out.append((a2, b2, s))
    return out


# ------------------------------------------------------------------ dasha
def _md_list(moon_lon: float, birth_jd: float, upto_jd: float):
    idx = int(moon_lon // NAK_SPAN) % 9
    frac = (moon_lon % NAK_SPAN) / NAK_SPAN
    years = DASHA_SEQ[idx][1]
    end = birth_jd + years * YEAR * (1 - frac)
    start = end - years * YEAR
    out = []
    while True:
        out.append({"idx": idx, "lord": DASHA_SEQ[idx][0], "years": years, "full_start": start, "end": end})
        if end > upto_jd:
            return out
        idx = (idx + 1) % 9
        years = DASHA_SEQ[idx][1]
        start, end = end, end + years * YEAR


def _subs(full_start: float, years: float, idx: int):
    out, cur = [], full_start
    for k in range(9):
        j = (idx + k) % 9
        length_days = years * DASHA_SEQ[j][1] / 120.0 * YEAR
        out.append({"idx": j, "lord": DASHA_SEQ[j][0], "years": years * DASHA_SEQ[j][1] / 120.0,
                    "start": cur, "end": cur + length_days})
        cur += length_days
    return out


# ------------------------------------------------------------------ facts
def build_forecast(chart, birth_dt: datetime, utc_offset: float, start: date, end: date, fast=None) -> dict:
    """All facts for [start, end]. `chart` is chart_engine.calculate_chart(...) output."""
    _init_swe()  # settings are per thread: apply them here, in the thread that does the maths
    moon = next(p for p in chart.placements if p.planet == "Moon")
    moon_sign, lagna_sign = int(moon.longitude // 30), SIGNS.index(chart.ascendant_sign)
    birth_jd = _julian_day_ut(birth_dt, utc_offset)

    def to_jd(d: date) -> float:              # local midnight -> UT Julian day
        return swe.julday(d.year, d.month, d.day, 0.0) - utc_offset / 24.0

    def to_date(jd: float) -> date:           # UT Julian day -> local calendar date
        y, m, d, _ = swe.revjul(jd + utc_offset / 24.0)
        return date(y, m, d)

    start = max(start, to_date(birth_jd))
    if end < start:
        end = start
    lo, hi = to_jd(start), to_jd(end) + 1.0
    span_years = (hi - lo) / YEAR
    house_m = lambda s: (s - moon_sign) % 12 + 1
    house_l = lambda s: (s - lagna_sign) % 12 + 1

    # --- dasha timeline
    mds = []
    for md in _md_list(moon.longitude, birth_jd, hi):
        if md["end"] <= lo or md["full_start"] >= hi:
            continue
        item = {"lord": md["lord"], "start": to_date(max(md["full_start"], birth_jd)), "end": to_date(md["end"]),
                "antardashas": []}
        if span_years <= 12:
            for ad in _subs(md["full_start"], md["years"], md["idx"]):
                if ad["end"] <= lo or ad["start"] >= hi:
                    continue
                a = {"lord": ad["lord"], "start": to_date(max(ad["start"], birth_jd)), "end": to_date(ad["end"]),
                     "pratyantardashas": []}
                if (hi - lo) <= 400:
                    for pd in _subs(ad["start"], ad["years"], ad["idx"]):
                        if pd["end"] <= lo or pd["start"] >= hi:
                            continue
                        a["pratyantardashas"].append({"lord": pd["lord"], "start": to_date(max(pd["start"], birth_jd)),
                                                      "end": to_date(pd["end"])})
                item["antardashas"].append(a)
        mds.append(item)

    # --- transits
    transits, stations = {}, []
    sat_all = None
    use_fast = fast if fast is not None else (hi - lo) <= 120     # Sun/Mars/Mercury/Venus: monthly detail only
    for planet in ("Jupiter", "Saturn", "Rahu") + (FAST if use_fast else ()):
        pad = 8 * YEAR if planet == "Saturn" else 0.0          # Saturn is padded so Sade Sati bounds are complete
        segs, sts = _scan(planet, lo - pad, hi + pad)
        if planet == "Saturn":
            sat_all = segs
        transits[planet] = [{
            "sign": SIGNS[s], "start": to_date(a), "end": to_date(b) if b < hi else end,
            "house_from_moon": house_m(s), "house_from_lagna": house_l(s),
            "favourable": (house_m(s) in FAVOURABLE[planet]) if planet in FAVOURABLE else None,
        } for a, b, s in _clip(segs, lo, hi)]
        if planet in STATION_PLANETS and span_years <= 5:
            stations += [{"planet": planet, "date": to_date(t), "kind": k, "sign": SIGNS[s]}
                         for t, k, s in sts if lo <= t <= hi]
    transits["Ketu"] = [{**seg, "sign": SIGNS[(SIGNS.index(seg["sign"]) + 6) % 12],
                         "house_from_moon": house_m((SIGNS.index(seg["sign"]) + 6) % 12),
                         "house_from_lagna": house_l((SIGNS.index(seg["sign"]) + 6) % 12), "favourable": None}
                        for seg in transits["Rahu"]]
    stations.sort(key=lambda e: e["date"])

    # --- Sade Sati (Saturn in the 12th, 1st, 2nd sign from natal Moon), merged spans touching the window
    spans, cur = [], None
    for a, b, s in sat_all:
        if house_m(s) in (12, 1, 2):
            if cur and a - cur["end_jd"] < 450:      # a short retrograde dip out of these signs does not end the cycle
                cur["end_jd"] = b
            else:
                cur = {"start_jd": a, "end_jd": b}
                spans.append(cur)
    sade = [{"start": to_date(sp["start_jd"]), "end": to_date(sp["end_jd"])} for sp in spans
            if sp["end_jd"] > lo and sp["start_jd"] < hi]

    # --- natal positions of every planet named in the timeline
    named = {"Jupiter", "Saturn", "Rahu", "Ketu"}
    for md in mds:
        named.add(md["lord"])
        for ad in md["antardashas"]:
            named.add(ad["lord"])
    natal = {}
    for p in chart.placements:
        if p.planet in named:
            natal[p.planet] = {"sign": p.sign, "house": p.house, "nakshatra": p.nakshatra, "pada": p.pada,
                               "retrograde": p.retrograde}
    for _p, _segs in list(transits.items()):                        # a planet that changes sign on the very last day leaves a one-day piece: drop it
        kept = [sg for sg in _segs if sg["end"] > sg["start"]]
        transits[_p] = kept or _segs
    return {"window": {"start": start, "end": end, "years": round(span_years, 2)},
            "moon_sign": SIGNS[moon_sign], "lagna": chart.ascendant_sign,
            "mahadashas": mds, "transits": transits, "stations": stations,
            "sade_sati": sade, "natal": natal}


def forecast_text(facts: dict, label: str = "") -> str:
    w = facts["window"]
    L = ["FORECAST FACTS (computed with Swiss Ephemeris, sidereal Lahiri; dates are local calendar dates)",
         f"Period: {w['start']} to {w['end']}" + (f" ({label})" if label else ""),
         f"Natal reference: Moon in {facts['moon_sign']}, Lagna {facts['lagna']}. Houses are whole-sign.", "",
         "DASHA TIMELINE (Vimshottari)"]
    for md in facts["mahadashas"]:
        L.append(f"- {md['lord']} Mahadasha {md['start']} to {md['end']}")
        for ad in md["antardashas"]:
            L.append(f"    - {ad['lord']} Antardasha {ad['start']} to {ad['end']}")
            for pd in ad["pratyantardashas"]:
                L.append(f"        - {pd['lord']} Pratyantardasha {pd['start']} to {pd['end']}")
    if facts["natal"]:
        L += ["", "NATAL POSITION OF THE PLANETS NAMED ABOVE"]
        for name, n in facts["natal"].items():
            L.append(f"- {name}: {n['sign']}, house {n['house']}, {n['nakshatra']} pada {n['pada']}"
                     + (" (retrograde)" if n["retrograde"] else ""))
    L += ["", "TRANSITS (house counted from the natal Moon / from the Lagna)"]
    if w["years"] > 12:                       # long period: one compact line per planet keeps room for book passages
        L.append("Format: start date, sign (house from Moon/house from Lagna), * = a supportive position in the classical transit rules.")
        for planet in ("Jupiter", "Saturn", "Rahu"):
            parts = []
            for seg in facts["transits"][planet]:
                star = "*" if seg["favourable"] else ""
                part = f"{seg['start']} {seg['sign']} ({seg['house_from_moon']}/{seg['house_from_lagna']}){star}"
                if planet == "Rahu":
                    ket = next(k for k in facts["transits"]["Ketu"] if k["start"] == seg["start"])
                    part += f" with Ketu in {ket['sign']} ({ket['house_from_moon']})"
                parts.append(part)
            L.append(f"{planet}: " + "; ".join(parts))
        L.append("")
        if facts["sade_sati"]:
            L.append("SADE SATI cycles: " + "; ".join(f"{s['start']} to {s['end']}" for s in facts["sade_sati"]))
        else:
            L.append("SADE SATI: none during this period.")
        L.append("This is a long period: summarise it Mahadasha by Mahadasha rather than year by year.")
        return "\n".join(L)
    for planet in ("Jupiter", "Saturn", "Rahu") + tuple(p for p in FAST if p in facts["transits"]):
        L.append(f"{planet}:")
        for seg in facts["transits"][planet]:
            line = (f"- {seg['start']} to {seg['end']}: in {seg['sign']}, {_ordinal(seg['house_from_moon'])} from Moon, "
                    f"{_ordinal(seg['house_from_lagna'])} from Lagna")
            if seg["favourable"] is not None:
                line += " [supportive position in the classical transit rules]" if seg["favourable"] else " [not among the supportive positions]"
            if planet == "Rahu":
                ket = next(k for k in facts["transits"]["Ketu"] if k["start"] == seg["start"])
                line += f"; Ketu in {ket['sign']}, {_ordinal(ket['house_from_moon'])} from Moon"
            L.append(line)
    if facts["stations"]:
        L.append("Retrograde and direct stations:")
        for e in facts["stations"]:
            L.append(f"- {e['date']}: {e['planet']} turns {e['kind']} in {e['sign']}")
    L.append("")
    if facts["sade_sati"]:
        L.append("SADE SATI (Saturn in the 12th, 1st or 2nd sign from the natal Moon) cycles touching this period: "
                 + "; ".join(f"{s['start']} to {s['end']}" for s in facts["sade_sati"])
                 + ". Saturn may step briefly out of these signs while retrograde; the transit list above gives the exact dates.")
    else:
        L.append("SADE SATI: Saturn is not in the 12th, 1st or 2nd sign from the natal Moon during this period.")
    return "\n".join(L)


# ------------------------------------------------------------------ retrieval + prompt
def retrieval_queries(facts: dict, chart, max_queries: int = 24):
    """Queries for the book database, interleaved so every kind of fact gets coverage."""
    long_range = facts["window"]["years"] > 12
    dasha_q, transit_q, natal_q = [], [], []
    for md in facts["mahadashas"]:
        dasha_q.append(f"{md['lord']} mahadasha results")
        if not long_range:
            for ad in md["antardashas"]:
                dasha_q.append(f"{ad['lord']} antardasha bhukti in {md['lord']} mahadasha results")
    for planet in ("Saturn", "Jupiter", "Rahu", "Ketu") + tuple(p for p in FAST if p in facts["transits"]):
        seen = set()
        for seg in facts["transits"][planet]:
            h = seg["house_from_moon"]
            if h not in seen:
                seen.add(h)
                transit_q.append(f"{planet} transit gochara {_ordinal(h)} house from Moon results")
    if facts["sade_sati"]:
        transit_q.append("Saturn transit 12th 1st 2nd house from Moon sade sati results")
    for p in chart.placements:
        if p.planet in facts["natal"]:
            retro = "retrograde" if p.retrograde else ""
            natal_q.append(" ".join(f"{p.planet} in {p.sign} {_ordinal(p.house)} house {retro} {p.nakshatra} effects".split()))
    out, groups = [], [dasha_q, transit_q, natal_q]
    for i in range(max(len(g) for g in groups)):
        for g in groups:
            if i < len(g) and g[i] not in out:
                out.append(g[i])
    return out[:max_queries]


def merge_queries(base, forecast):
    """Alternate the chart-question queries with the forecast queries so neither crowds the other out."""
    out = []
    for i in range(max(len(base), len(forecast))):
        for g in (base, forecast):
            if i < len(g) and g[i] not in out:
                out.append(g[i])
    return out


def round_robin(results_per_query, cap: int):
    """Pick passages fairly across queries. results_per_query: list of [(doc, source), ...]."""
    picked, seen, depth = [], set(), 0
    while len(picked) < cap and any(depth < len(r) for r in results_per_query):
        for r in results_per_query:
            if depth < len(r):
                doc, src = r[depth]
                key = doc[:80]
                if key not in seen:
                    seen.add(key)
                    picked.append((doc, src))
                    if len(picked) >= cap:
                        break
        depth += 1
    return picked


FORECAST_RULES = """RULES FOR THIS FORECAST
1. Every date, sign and house in FORECAST FACTS is exact. Never change them and never add dates or planetary positions of your own.
2. Interpret each period ONLY from the classical text passages below, and name the source in square brackets after each interpretation.
3. If the passages do not cover a period or planet, write "The books available do not cover this" for that item. Do not fill gaps from general knowledge.
4. Do not say an event WILL happen unless a passage states it. Describe tendencies the text describes.
5. Organise the answer by time period, earliest first. Be specific to this chart; do not write anything that could apply to anyone.
6. For which planet rules which house, use ONLY the HOUSE LORDS list. Never work lordship out yourself.
7. Natal positions (NATAL POSITION list) and transit positions (TRANSITS list) are different. Always say "natal" or "transiting", and never mix the two.
8. "Supportive position" means generally supportive, not specifically for one area of life. Say it that way, and never mention "tables" or "supplied" material; say "the classical texts".
9. Name a book only for a statement that comes from a passage of that book. Never use quotation marks.
10. For questions about timing (marriage, career, money), use BOTH the life periods (dashas) and the planet movements in the facts. Say which periods look most supportive and why. Never rely on planet movements alone."""


def prepare_forecast(chart, birth_dt: datetime, utc_offset: float, question: str, today: date | None = None):
    """None if the question is not about a period; otherwise everything the reading needs."""
    win = parse_period(question, today, birth_year=birth_dt.year)
    if win is None:
        return None
    facts = build_forecast(chart, birth_dt, utc_offset, win.start, win.end)
    text = forecast_text(facts, win.label)
    if win.note:
        text += "\nNOTE: " + win.note
    days = (win.end - win.start).days
    plain_args = {"label": win.label, "short": days <= 130, "summary": 130 < days <= 2300,   # up to about 6 years: stretch list + book passages
                  "meanings": days <= 2300, "note": win.note}
    plain = plain_facts_text(facts, chart, win.label, short=plain_args["short"], summary=plain_args["summary"])
    if win.note:
        plain += "\nNOTE: " + win.note
    return {"window": win, "facts": facts, "facts_text": text, "plain_facts": plain, "plain_args": plain_args,
            "must": must_mention(facts) if 130 < days <= 2300 else [],
            "queries": retrieval_queries(facts, chart, 16), "rules": FORECAST_RULES,
            "passage_cap": 24, "per_query": 3, "max_tokens": 3000}


def forecast_prompt(seeker, chart, chart_summary, fc, rules_text, question):
    if fc.get("plain_facts"):                                       # plain-language answer for ordinary readers
        return f"""You are writing a personal guidance answer for {seeker}.

{fc["plain_facts"]}

CLASSICAL PASSAGES (the ONLY source for meaning; sources are named in brackets):
{rules_text}

{QA_FORECAST_RULES}

Question: {question}

Address {seeker} as "you"."""
    return f"""You are an expert Vedic astrologer.
Person: {seeker}
Ascendant: {chart.ascendant_sign} {chart.ascendant_deg:.2f} ({chart.ascendant_nakshatra})
Placements:
{chart_summary}

{fc["facts_text"]}

Classical texts (the ONLY source material for interpretation):
{rules_text}

{fc["rules"]}

Question: {question}

{QA_STYLE}

Go period by period, earliest first, addressing {seeker} directly. Do not open with a sentence stating the period covered."""


# Groq's free plan allows 8,000 tokens per minute, counting the prompt AND the answer budget.
TPM_BUDGET = int(os.getenv("GROQ_TPM_BUDGET", "7200"))


def est_tokens(text: str) -> int:
    return int(len(text) / 3.5) + 1          # deliberately cautious estimate


def fit_prompt(make_prompt, retrieved, sources, max_tokens: int, budget: int | None = None, min_passages: int = 4):
    """Drop the least relevant passages (from the end) until prompt + answer fit Groq's per-minute limit."""
    budget = budget or TPM_BUDGET
    n = len(retrieved)
    while True:
        prompt = make_prompt(retrieved[:n], sources[:n])
        if est_tokens(prompt) + max_tokens <= budget or n <= min_passages:
            return prompt, list(retrieved[:n]), list(sources[:n])
        n -= 1


if __name__ == "__main__":  # quick check without any AI:  python forecast_engine.py "2027 to 2030"
    import sys
    from chart_engine import calculate_chart
    q = sys.argv[1] if len(sys.argv) > 1 else "next 2 years"
    bd = datetime(1969, 11, 29, 5, 30)
    ch = calculate_chart(bd, 28.8955, 76.6066, 5.5)
    fc = prepare_forecast(ch, bd, 5.5, q)
    print(fc["facts_text"] if fc else "That question does not name a period.")
