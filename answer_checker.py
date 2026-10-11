"""
answer_checker.py - stops wrong facts reaching the user.

1. house_lords_text(): gives the AI the house lords already calculated, so it never works them out itself.
2. check_answer(): reads the AI's answer and flags every statement that contradicts the calculated
   chart or timeline (house lords, planet/sign/house, "Nth from Lagna/Moon", dasha dates, transit dates)
   and any quotation marks (book text must be paraphrased).
The checker is deliberately conservative: it only flags what it can prove wrong.
"""
import os
import re
import unicodedata
from datetime import date, timedelta

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio",
         "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
PLANETS = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]
SIGN_LORD = ["Mars", "Venus", "Mercury", "Moon", "Sun", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Saturn", "Jupiter"]
SLOW = ["Jupiter", "Saturn", "Rahu", "Ketu"]
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
PL = "|".join(PLANETS)
SG = "|".join(SIGNS)
ORD = r"(\d{1,2})(?:st|nd|rd|th)"
DASH = r"(?:[\-\u2010\u2011\u2012\u2013\u2014\u2192]|->|to|till|until)"
MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
TOKEN = rf"(\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\s+{MON}\s+\d{{4}}|{MON}\s+\d{{4}}|{MON}|\d{{4}}(?!\d))"
RANGE = re.compile(rf"{TOKEN}\s*{DASH}\s*{TOKEN}", re.I)
DASHA_WORD = re.compile(r"\b(maha\s*dash?a|antar\s*dash?a|pratyantar\s*dash?a|bhukti)\b", re.I)
LORD_WORDS = {"ascendant": 1, "lagna": 1, "home": 4, "career house": 10}
# "lord of the 3rd and 6th houses", "Mars rules the 2nd and 7th houses", "Mars, whose lordship is over the 2nd and 7th"
OWN_RE = re.compile(
    r"(?:\blords?\s+of|\brulers?\s+of|\brules?|\bowns|\bgoverns|\blordship\s+(?:is\s+)?over)\s+(?:the\s+|your\s+|that\s+|this\s+)?"
    r"((?:(?:\d{1,2}(?:st|nd|rd|th)(?:\s+houses?)?|ascendant|lagna|home|career house)(?:\s*(?:,|and|&)\s*)?)+)", re.I)
# a planet named together with a dasha level, in classical or plain words
LEVEL_RE = re.compile(
    rf"\b({PL})(?:'s)?[\s-]+(?:(maha|antar|pratyantar)\s*-?dash?a|(bhukti)|(main period)|(sub[- ]period)|(short phase))", re.I)
LEVEL_NAME = {"maha": "Mahadasha", "antar": "Antardasha", "pratyantar": "Pratyantardasha",
              "bhukti": "Antardasha", "main": "Mahadasha", "sub": "Antardasha", "short": "Pratyantardasha"}
DASHA_MARGIN = timedelta(days=60)
NEG_WORDS = re.compile(r"\b(?:quiet|neutral|not[\s-]+supportive|non[\s-]+supportive|unsupportive|not[\s-]+favou?rable)\b", re.I)
NEGATED = re.compile(r"\b(?:not|non|un)[\s-]*(?:supportive|favou?rable)\b", re.I)
POS_WORDS = re.compile(r"\b(?:supportive|favou?rable|supports?|supported|supporting)\b", re.I)
BECOMES = re.compile(r"\b(?:becomes?|turns?|moves? into a|shifts? to a)\s+(?:also\s+)?(?:a\s+)?supportive\b", re.I)
VERDICT_PLANETS = ("Jupiter", "Saturn", "Sun", "Mars", "Mercury", "Venus")
TOL = timedelta(days=35)
RETRY_WAIT = int(os.getenv("GROQ_RETRY_WAIT", "62"))   # seconds; Groq free plan resets its token limit every minute


def _ord(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def house_lords(lagna_sign):
    li = SIGNS.index(lagna_sign)
    out = {p: [] for p in PLANETS[:7]}
    for h in range(1, 13):
        out[SIGN_LORD[(li + h - 1) % 12]].append(h)
    return out


def house_lords_text(lagna_sign):
    hl = house_lords(lagna_sign)
    return ("HOUSE LORDS for " + lagna_sign + " Lagna (already calculated; use these and never work lordship out yourself): "
            + "; ".join(f"{p} rules the {' and '.join(_ord(h) for h in hs)}" for p, hs in hl.items())
            + ". Rahu and Ketu rule no house.")


def _plain(text):
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("\u2019", "'").replace("\u00a0", " ").replace("\u202f", " ")
    t = re.sub(r"[\u2010\u2011\u2012\u2212]", "-", t)          # special hyphens -> "-"
    t = re.sub(r"(\d)-(st|nd|rd|th)\b", r"\1\2", t)           # "3-rd" -> "3rd"
    t = re.sub(r"(\d(?:st|nd|rd|th))\s*-\s*(house|sign)", r"\1 \2", t, flags=re.I)   # "6th-house" -> "6th house"
    t = t.replace("\u200b", "")
    return t


def _clauses(text):
    return [c for c in re.split(r"(?<=[.!?])\s+|\n+|;|\t", text) if c.strip()]


def _token_span(tok, year_hint=None):
    """(earliest, latest) date for a date token; year_hint for month-only tokens."""
    t = tok.strip().lower().rstrip(".")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d, d
    m = re.fullmatch(rf"(\d{{1,2}})\s+({MON})\s+(\d{{4}})", t)
    if m:
        d = date(int(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)))
        return d, d
    m = re.fullmatch(rf"({MON})\s+(\d{{4}})", t)
    if m:
        y, mo = int(m.group(2)), MONTHS[m.group(1)[:3]]
        last = (date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1))
        return date(y, mo, 1), last
    m = re.fullmatch(rf"({MON})", t)
    if m and year_hint:
        y, mo = year_hint, MONTHS[m.group(1)[:3]]
        return date(y, mo, 1), date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
    m = re.fullmatch(r"(\d{4})", t)
    if m:
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31)
    return None


def _ranges(text):
    """Lenient claimed spans: (latest possible start, earliest possible end)."""
    out = []
    for m in RANGE.finditer(text):
        end = _token_span(m.group(2))
        if not end:
            continue
        start = _token_span(m.group(1), year_hint=end[0].year)
        if not start:
            continue
        if start[0] > end[1]:                       # "Nov - Feb 2031" -> Nov of the previous year
            start = _token_span(m.group(1), year_hint=end[0].year - 1)
        lo, hi = start[1], end[0]
        if lo <= hi:
            out.append((lo, hi, m.group(0)))
    return out


def check_answer(answer, chart, facts=None):
    """Return a list of plain-English problems. Empty list = nothing provably wrong."""
    issues = []
    text = _plain(answer)
    lagna = chart.ascendant_sign
    natal = {p.planet: (p.sign, p.house) for p in chart.placements}
    moon_sign = natal["Moon"][0]
    lords = house_lords(lagna)
    seen = set()

    def add(msg):
        if msg not in seen:
            seen.add(msg)
            issues.append(msg)

    # 1. quotation marks (book text must be paraphrased)
    quotes = re.findall(r"[\u201c\"]([^\u201d\"]{12,}?)[\u201d\"]", text)
    if quotes:
        add(f"Quotation marks were used {len(quotes)} time(s). Book text must be put in your own words, never quoted.")

    section = None
    prev_cl = ""
    for cl in _clauses(text):
        # track which Antardasha heading we are under (e.g. "2. Sun Mahadasha - Mercury Antardasha (dates)")
        if re.fullmatch(r"\s*\d+\.\s*", cl):          # a new numbered section starts
            section = None
            continue
        hm = re.search(rf"\b({PL})\s+(antar\s*dash?a|bhukti)\b", cl, re.I)
        if hm and len(cl) < 160:                          # a heading such as "Mercury Antardasha (2028-11-08 -> 2029-09-15)"
            rs = _ranges(cl[hm.end():hm.end() + 45])
            if rs:
                section = (rs[0][0], rs[0][1], f"{hm.group(1)} {hm.group(2)}")
        # 2. house lords: "lord of the/your Nth", "rules the Nth and Mth", "both ... are the lords of the Nth"
        for m in OWN_RE.finditer(cl):
            hs = [int(x) if x else LORD_WORDS[y.lower()] for x, y in re.findall(r"(\d{1,2})(?:st|nd|rd|th)|\b(ascendant|lagna|home|career house)\b", m.group(1), re.I)]
            before = re.findall(rf"\b({PL})\b", cl[:m.start()])
            if re.search(r"\b(both|all|each|these)\b", cl[:m.start()], re.I):       # "Both planets are the lords of..."
                cands = list(dict.fromkeys(re.findall(rf"\b({PL})\b", prev_cl + " " + cl[:m.start()])))
            else:
                cands = before[-1:]
            for p in cands:
                for h in hs:
                    if p in ("Rahu", "Ketu"):
                        add(f"{p} is called lord of the {_ord(h)}, but Rahu and Ketu rule no house.")
                    elif p in lords and h not in lords[p]:
                        add(f"{p} is called lord of the {_ord(h)}, but for {lagna} Lagna {p} rules the "
                            f"{' and '.join(_ord(x) for x in lords[p])}.")
        prev_cl = cl

        # 3. "Nth from the Lagna / Moon" must match the sign named just before it, or the planet's real position
        for m in re.finditer(rf"((?:\d{{1,2}}(?:st|nd|rd|th)\s+(?:house\s+)?(?:to|and|or|&|,)\s+(?:the\s+)?)*){ORD}\s+(?:house\s+)?(from|of)\s+(?:the\s+|your\s+)?(?:natal\s+)?(lagna|moon|ascendant)\b", cl, re.I):
            if m.group(3).lower() == "of" and m.group(4).lower() == "moon":
                continue
            n = int(m.group(2))
            earlier = [int(x) for x in re.findall(r"(\d{1,2})(?:st|nd|rd|th)", m.group(1))]   # "from the 12th to the 10th house of ..."
            refname = "Moon" if m.group(4).lower() == "moon" else "Lagna"
            ref = moon_sign if refname == "Moon" else lagna
            signs = re.findall(rf"\b({SG})\b", cl[max(0, m.start() - 60):m.start()])
            if signs and not earlier:
                s_ = signs[-1]
                expect = (SIGNS.index(s_) - SIGNS.index(ref)) % 12 + 1
                if expect != n:
                    add(f"{s_} is called the {_ord(n)} from the {refname}, but it is the {_ord(expect)}.")
                continue
            if not facts:
                continue
            pls = set(re.findall(rf"\b({PL})\b", re.sub(r"(?:from|of)\s+(?:the\s+|your\s+)?(?:natal\s+)?moon\b", "", cl, flags=re.I)))
            if len(pls) != 1 or next(iter(pls)) not in facts["transits"]:
                continue
            p = next(iter(pls))
            key = "house_from_moon" if refname == "Moon" else "house_from_lagna"
            allowed = {seg[key] for seg in facts["transits"][p]}
            if p in natal:
                nsign, nhouse = natal[p]
                allowed.add((SIGNS.index(nsign) - SIGNS.index(moon_sign)) % 12 + 1 if refname == "Moon" else nhouse)
            for k in earlier + [n]:
                if k not in allowed:
                    add(f"{p} is called the {_ord(k)} from the {refname}, but during this period it is the "
                        f"{' / '.join(_ord(x) for x in sorted(allowed))} from the {refname}. "
                        f"Do not apply a classical result for the {_ord(k)} to this chart.")

        # 3b. "supportive" / "quiet" must match the calculated verdict of the one planet the clause is about
        if facts:
            named = set(re.findall(rf"\b({PL})\b", cl))
            if len(named) == 1 and next(iter(named)) in VERDICT_PLANETS and next(iter(named)) in facts["transits"]:
                p = next(iter(named))
                V = {seg.get("favourable") for seg in facts["transits"][p] if seg["end"] > seg["start"]} - {None}
                has_neg = bool(NEG_WORDS.search(cl))
                has_pos = bool(POS_WORDS.search(NEGATED.sub("", cl)))
                if V == {True}:
                    if has_neg and not has_pos:
                        add(f"{p} is a supportive position for this whole period, but the text calls it quiet or not supportive.")
                    elif BECOMES.search(cl):
                        add(f"{p} is already supportive at the start of this period and stays so; it does not become supportive during it.")
                elif V == {False} and has_pos and not has_neg:
                    add(f"{p} is not a supportive position at any time in this period, but the text calls it supportive.")

        # 4. dasha dates
        if DASHA_WORD.search(cl):
            for m in re.finditer(rf"\b({PL})\s+(maha\s*dash?a|antar\s*dash?a|pratyantar\s*dash?a|bhukti)\b", cl, re.I):
                if not facts:
                    continue
                after = cl[m.end():m.end() + 45]
                rs = _ranges(after)
                if not rs:
                    continue
                lo, hi, raw = rs[0]
                lvl = m.group(2).lower().replace(" ", "")
                lord = m.group(1)
                periods = []
                for md in facts["mahadashas"]:
                    if lvl.startswith("maha"):
                        periods.append(md)
                    for ad in md["antardashas"]:
                        if lvl.startswith("antar") or lvl == "bhukti":
                            periods.append(ad)
                        if lvl.startswith("pratyantar"):
                            periods += ad["pratyantardashas"]
                periods = [p for p in periods if p["lord"] == lord]
                if periods and not any(p["start"] - TOL <= lo and hi <= p["end"] + TOL for p in periods):
                    real = "; ".join(f"{p['start']} to {p['end']}" for p in periods)
                    add(f"{lord} {m.group(2)} is dated {raw}, but the calculated dates are {real}.")
        # 5. transit dates (only when the clause names exactly one slow planet and one sign, and no dasha)
        elif facts:
            pls = set(re.findall(rf"\b({PL})\b", re.sub(r"from\s+(?:the\s+)?(?:natal\s+)?moon\b", "", cl, flags=re.I)))
            sgs = set(re.findall(rf"\b({SG})\b", cl))
            if len(pls) == 1 and len(sgs) == 1 and next(iter(pls)) in facts["transits"]:
                p, s = next(iter(pls)), next(iter(sgs))
                segs = [x for x in facts["transits"][p] if x["sign"] == s]
                spans = _ranges(cl)
                cont = re.search(rf"\b(?:continu|remain|stay)\w*\b[^.;]{{0,40}}?\b(?:through|until|till)\s+{TOKEN}", cl, re.I)
                first = re.search(TOKEN, cl, re.I)
                if cont and first and first.start() < cont.start():
                    a_, b_ = _token_span(first.group(1)), _token_span(cont.group(1))
                    if a_ and b_ and a_[1] <= b_[0]:
                        spans.append((a_[1], b_[0], f"{first.group(1)} through {cont.group(1)}"))
                for lo, hi, raw in spans:
                    if section and not re.search(r"\b(before|after|earlier|later|previous|preceding|following)\b", cl, re.I) \
                            and (hi + TOL < section[0] or lo - TOL > section[1]):
                        add(f"{p} in {s} ({raw}) is discussed under the {section[2]}, but it falls outside that period ({section[0]} to {section[1]}).")
                    if not segs:
                        add(f"{p} is placed in {s} for {raw}, but {p} is not in {s} at any time in this period.")
                    elif not any(x["start"] - TOL <= lo and hi <= x["end"] + TOL for x in segs):
                        real = "; ".join(f"{x['start']} to {x['end']}" for x in segs)
                        add(f"{p} is placed in {s} for {raw}, but {p} is in {s} only {real}.")

    # 5b. dasha levels: "Sun's antardasha" / "Venus in Rahu's Mahadasha" must match the calculated periods
    if facts and facts.get("mahadashas"):
        w0, w1 = facts["window"]["start"] - DASHA_MARGIN, facts["window"]["end"] + DASHA_MARGIN
        def lords_at(level):
            out = []
            for md in facts["mahadashas"]:
                if md["start"] <= w1 and md["end"] >= w0 and level == "Mahadasha":
                    out.append(md["lord"])
                for ad in md["antardashas"]:
                    if ad["start"] <= w1 and ad["end"] >= w0 and level == "Antardasha":
                        out.append(ad["lord"])
                    for pd in ad["pratyantardashas"]:
                        if pd["start"] <= w1 and pd["end"] >= w0 and level == "Pratyantardasha":
                            out.append(pd["lord"])
            return list(dict.fromkeys(out))
        for m in LEVEL_RE.finditer(text):
            key = (m.group(2) or m.group(3) or m.group(4) or m.group(5) or m.group(6)).lower().split()[0].split("-")[0]
            level = LEVEL_NAME.get(key)
            if not level:
                continue
            real = lords_at(level)
            if real and m.group(1).title() not in real:
                add(f"{m.group(1).title()} is named as the {level} lord, but during this period the {level} lord is "
                    f"{' then '.join(real)}. (Mahadasha: {', '.join(lords_at('Mahadasha'))}; "
                    f"Antardasha: {', '.join(lords_at('Antardasha'))}"
                    + (f"; Pratyantardasha: {', '.join(lords_at('Pratyantardasha'))}" if lords_at("Pratyantardasha") else "") + ".)")

    # 6. "Planet in Sign, Nth house" must match the birth chart (or a transit, counted from the Lagna)
    for m in re.finditer(rf"\b({PL})\b\s+in\s+({SG})\s*,?\s*(?:the\s+)?{ORD}\s+house(?!\s+from\s+(?:the\s+)?(?:natal\s+)?moon)", text, re.I):
        p, s, h = m.group(1), m.group(2), int(m.group(3))
        ok = natal.get(p) == (s, h)
        if not ok and facts and p in facts["transits"]:
            ok = any(x["sign"] == s for x in facts["transits"][p]) and (SIGNS.index(s) - SIGNS.index(lagna)) % 12 + 1 == h
        if not ok:
            ns, nh = natal[p]
            add(f"'{p} in {s}, {_ord(h)} house' is wrong: in the birth chart {p} is in {ns}, {_ord(nh)} house.")

    # 7. "Sign is the Nth from Sign"
    for m in re.finditer(rf"\b({SG})\b\s+(?:is|being)\s+the\s+{ORD}\s+(?:house\s+|sign\s+)?from\s+({SG})\b", text):
        a, n, b = m.group(1), int(m.group(2)), m.group(3)
        expect = (SIGNS.index(a) - SIGNS.index(b)) % 12 + 1
        if expect != n:
            add(f"{a} is called the {_ord(n)} from {b}, but it is the {_ord(expect)}.")
    return issues


def correction_block(issues):
    return ("\n\nA PREVIOUS DRAFT OF THIS ANSWER CONTAINED THESE ERRORS. Do not repeat them; use only the facts above:\n- "
            + "\n- ".join(issues))


def warning_text(issues):
    return ("\n\n---\n**Accuracy check:** these statements did not match your calculated chart and timeline, "
            "so please disregard them:\n" + "\n".join(f"- {i}" for i in issues))
