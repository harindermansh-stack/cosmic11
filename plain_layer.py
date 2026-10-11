"""
plain_layer.py - makes Cosmic 11 readable for ordinary people.

Why it exists: the first reports were written in textbook astrology ("Jupiter, lord of the 3rd house, transiting the
10th from the Lagna...") and the AI made mistakes in exactly that technical wording. So:
  * the AI now gets SIMPLE facts (no house numbers) and must write in everyday words;
  * the technical timeline is still calculated and shown, but by the engine, not by the AI;
  * jargon that slips through is detected (lint_plain) and the section is rewritten once.

Plain labels used everywhere:  main period = Mahadasha,  sub-period = Antardasha,  short phase = Pratyantardasha.
"""
import re
from datetime import date

PLANET_ORDER = ["Jupiter", "Saturn", "Rahu", "Ketu", "Sun", "Mars", "Mercury", "Venus"]
OWN = {"Sun": ["Leo"], "Moon": ["Cancer"], "Mars": ["Aries", "Scorpio"], "Mercury": ["Gemini", "Virgo"],
       "Jupiter": ["Sagittarius", "Pisces"], "Venus": ["Taurus", "Libra"], "Saturn": ["Capricorn", "Aquarius"]}
EXALTED = {"Sun": "Aries", "Moon": "Taurus", "Mars": "Capricorn", "Mercury": "Virgo", "Jupiter": "Cancer",
           "Venus": "Pisces", "Saturn": "Libra"}

GLOSSARY = {"main period": "Mahadasha", "sub-period": "Antardasha", "short phase": "Pratyantardasha"}


def _runs(tr):
    """Merge neighbouring pieces that have the same verdict (a sign change inside a supportive stretch is not a change)."""
    runs = []
    for seg in tr:
        if seg["end"] <= seg["start"]:
            continue
        v = seg.get("favourable")
        if runs and runs[-1]["v"] == v:
            runs[-1]["end"] = seg["end"]
            if seg["sign"] not in runs[-1]["signs"]:
                runs[-1]["signs"].append(seg["sign"])
        else:
            runs.append({"v": v, "start": seg["start"], "end": seg["end"], "signs": [seg["sign"]]})
    return runs


def _both(a_runs, b_runs):
    out = []
    for a in a_runs:
        for b in b_runs:
            st, en = max(a["start"], b["start"]), min(a["end"], b["end"])
            if st < en:
                out.append((st, en))
    return sorted(out)


def supportive_stretches(facts):
    """{'Jupiter': [(start, end)], 'Saturn': [...], 'both': [...]} - calculated, never written by the AI."""
    got = {}
    for p in ("Jupiter", "Saturn"):
        if p in facts["transits"]:
            got[p] = [(r["start"], r["end"]) for r in _runs(facts["transits"][p]) if r["v"]]
    if "Jupiter" in got and "Saturn" in got:
        j = [{"start": a, "end": b} for a, b in got["Jupiter"]]
        sa = [{"start": a, "end": b} for a, b in got["Saturn"]]
        got["both"] = _both(j, sa)
    return got


def supportive_lines(facts):
    got = supportive_stretches(facts)
    if not any(got.get(k) for k in ("Jupiter", "Saturn")):
        return []
    f = lambda lst: "; ".join(f"{fmt_date(a)} to {fmt_date(b)}" for a, b in lst) or "none in this period"
    L = ["SUPPORTIVE STRETCHES (calculated by the engine. Under 'Most supportive times' mention EVERY stretch listed here, with exactly these dates, "
         "starting with the ones where both planets are supportive. Add no others.)"]
    for p in ("Jupiter", "Saturn"):
        if p in got:
            L.append(f"- {p} supportive: {f(got[p])}")
    if "both" in got:
        L.append(f"- Both Jupiter and Saturn supportive together (the strongest windows): {f(got['both'])}")
    return L


def must_mention(facts):
    """Start dates the answer has to contain: all stretches when there are few, otherwise the strongest windows."""
    got = supportive_stretches(facts)
    singles = sorted({a for k in ("Jupiter", "Saturn") for a, _ in got.get(k, [])})
    both = sorted({a for a, _ in got.get("both", [])})
    return singles if len(singles) <= 8 else both


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def mentions_date(text, d):
    mo = _MONTHS[d.month - 1]
    return bool(re.search(rf"\b{d.day}\s+(?:{mo[:3]}|{mo})[a-z]*\.?,?\s+{d.year}\b", text, re.I))


def completeness_issues(text, dates):
    return [f"You left out the supportive stretch that starts {fmt_date(d)}. Mention every stretch listed under SUPPORTIVE STRETCHES, with its dates."
            for d in dates if not mentions_date(text, d)]


def fmt_date(d):
    return f"{d.day} {d.strftime('%b %Y')}"


def _span(seg, lo, hi):
    s, e = seg["start"], seg["end"]
    if s <= lo and e >= hi:
        return "for the whole period"
    if s <= lo:
        return f"until {fmt_date(e)}"
    if e >= hi:
        return f"from {fmt_date(s)}"
    return f"from {fmt_date(s)} to {fmt_date(e)}"


# ------------------------------------------------------------------ running periods (calculated, never written by the AI)
def running_segments(facts, short=True):
    """[{start, end, main, sub, short}] clipped to the window, merged when nothing changes."""
    lo, hi = facts["window"]["start"], facts["window"]["end"]
    out = []
    for md in facts["mahadashas"]:
        for ad in md["antardashas"]:
            pds = (ad["pratyantardashas"] if short else None) or [None]
            for pd in pds:
                node = pd or ad
                s = max(lo, node["start"], ad["start"], md["start"])
                e = min(hi, node["end"], ad["end"], md["end"])
                if s < e:
                    out.append({"start": s, "end": e, "main": md["lord"], "sub": ad["lord"],
                                "short": pd["lord"] if pd else None})
    if not out:                                                      # periods longer than 12 years: main periods only
        for md in facts["mahadashas"]:
            s, e = max(lo, md["start"]), min(hi, md["end"])
            if s < e:
                out.append({"start": s, "end": e, "main": md["lord"], "sub": None, "short": None})
    merged = []
    for seg in out:
        if merged and all(merged[-1][k] == seg[k] for k in ("main", "sub", "short")):
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(dict(seg))
    return merged


def strength_notes(chart, lords):
    notes = []
    for p in chart.placements:
        if p.planet in lords and p.planet in OWN:
            if p.sign == EXALTED.get(p.planet):
                notes.append(f"{p.planet} is exalted in your birth chart (very strong).")
            elif p.sign in OWN[p.planet]:
                notes.append(f"{p.planet} is in its own sign in your birth chart (strong).")
    return notes


# ------------------------------------------------------------------ simple facts for the AI
def plain_facts_text(facts, chart, label="", short=True, meanings=None, summary=False):
    w = facts["window"]
    lo, hi = w["start"], w["end"]
    L = ["CALCULATED FACTS (exact. Never change a date, planet or sign, and never add your own.)",
         f"Period: {fmt_date(lo)} to {fmt_date(hi)}" + (f" ({label})" if label else ""), "",
         "LIFE PERIODS RUNNING (use exactly the words main period, sub-period, short phase)"]
    segs = running_segments(facts, short)
    lords = set()
    for s in segs:
        lords |= {s["main"]} | ({s["sub"]} if s["sub"] else set()) | ({s["short"]} if s["short"] else set())
        txt = f"- {fmt_date(s['start'])} to {fmt_date(s['end'])}: {s['main']} main period" + (f", {s['sub']} sub-period" if s["sub"] else "")
        if s["short"]:
            txt += f", {s['short']} short phase"
        L.append(txt)
    L += ["", "PLANET MOVEMENTS AND THEIR CLASSICAL VERDICT",
          "(The words supportive / not supportive come from the classical transit rules of Phala Deepika. 'Not supportive' "
          "means a quiet or neutral time, never a harmful one. Use these verdicts exactly as given.)"]
    for planet in PLANET_ORDER:
        tr = facts["transits"].get(planet)
        if not tr:
            continue
        runs = _runs(tr)
        if not runs:
            continue
        if planet in ("Rahu", "Ketu"):
            segs = [x for x in tr if x["end"] > x["start"]]
            if len({x["sign"] for x in segs}) <= 1:
                L.append(f"- {planet}: stays in {segs[0]['sign']} (no classical verdict: never call it supportive or quiet)")
            else:
                moves = "; ".join(f"moves into {x['sign']} on {fmt_date(x['start'])}" for x in segs[1:])
                L.append(f"- {planet}: in {segs[0]['sign']} at first, then {moves} (no classical verdict: never call it supportive or quiet)")
            continue
        label = lambda v: "supportive" if v else "not supportive"
        if len(runs) == 1:
            r = runs[0]
            L.append(f"- {planet}: {label(r['v'])} for the whole period (in {' then '.join(r['signs'])})")
        else:
            bits = []
            for k, r in enumerate(runs):
                where = "in " + " then ".join(r["signs"])
                if k == 0:
                    bits.append(f"{label(r['v'])} until {fmt_date(r['end'])} ({where})")
                elif k == len(runs) - 1:
                    bits.append(f"{label(r['v'])} from {fmt_date(r['start'])} ({where})")
                else:
                    bits.append(f"{label(r['v'])} from {fmt_date(r['start'])} to {fmt_date(r['end'])} ({where})")
            L.append(f"- {planet}: " + "; ".join(bits))
    if meanings:
        L += ["", "WHAT THE CLASSICAL TEXTS SAY ABOUT THESE POSITIONS (use for meaning only: put the idea in plain words and add the book "
                  "in [brackets]; never write house numbers; ignore a text that contradicts the supportive / quiet verdict; "
                  "skip any hardship it describes)"]
        for (planet, sign), (src, snippet) in meanings.items():
            L.append(f"- {planet} in {sign} [{src}]: {snippet}")
    if summary:
        sl = supportive_lines(facts)
        if sl:
            L += [""] + sl
    L.append("")
    L.append("SADE SATI: " + ("running during this period." if facts["sade_sati"] else "not running during this period."))
    notes = strength_notes(chart, lords)
    if notes:
        L += ["", "STRONG PLACEMENTS IN THE BIRTH CHART (planets of the running periods)"] + [f"- {n}" for n in notes]
    return "\n".join(L)


# ------------------------------------------------------------------ prompts
STYLE_RULES = """HOW TO WRITE (very important)
1. Write for an ordinary adult who knows nothing about astrology, as a kind friend would explain it. Short sentences (under 20 words). Everyday words.
2. NEVER use: house numbers (such as "10th house"), "Lagna", "ascendant", "lord of", "from the Moon", "Mahadasha", "Antardasha", "Pratyantardasha", "dasha", "transit table", or words like "supplied", "provided", "the passages". Planet and sign names are fine.
3. Say "main period", "sub-period" and "short phase" for the life periods, exactly as in the facts.
4. Use only the dates, planets and signs in CALCULATED FACTS. Never add a date.
5. The words "supportive" and "not supportive" come from the facts. Use them exactly as given:
   - supportive = "a supportive time"; not supportive = "a quiet or neutral time".
   - Say a planet "becomes" or "turns" supportive only if the facts show that change, on that date. If the facts say "for the whole period", it is supportive or quiet all month, and it does not change.
   - Rahu and Ketu have no verdict: describe only their move from one sign to another, and never call them quiet or supportive.
6. Use the passages only for what a position MEANS. Translate the idea into plain words and name the book in square brackets, for example [Phala Deepika], only for meaning that comes from a passage. Never use quotation marks.
7. NEVER predict or hint at anything specific or negative: no loss, strain, stress, tension, pressure, trouble, health, illness, accident, separation or death. Never say something is unlikely or risky, and never tell the reader to avoid anything. A quiet time is only "quiet" or "neutral".
8. No remedies, gemstones, rituals or purchases, and no advice about investing.
9. Write something for each area using the facts. Write the sentence "The books do not cover this area for this period." ONLY for an area where you have nothing at all to say, and then write nothing else in that area. Never write it after other sentences.
10. If little changes, say it is a steady period in one sentence. Never invent events or tendencies to fill space.
11. Do not state the period covered or the month number; the page shows it.
12. Do not mention Mercury or any planet turning backwards (retrograde); the facts do not cover it.
13. Where the facts include WHAT THE CLASSICAL TEXTS SAY, use it to explain what a supportive or quiet position tends to mean for the person, in plain words, and add that book in [brackets]. Never copy house numbers, hardships or predictions from it. If a text does not fit the verdict, leave it out."""

MONTH_FORMAT = """FORMAT (exactly)
**In short:** one or two plain sentences giving the main picture of the month.
### Career and work
two or three short sentences
### Relationships
two or three short sentences
### Money
two or three short sentences
Under 170 words in total. No other headings."""

YEAR_FORMAT = """FORMAT (exactly)
**In short:** two plain sentences giving the main picture of the year.
### Career and work
three or four short sentences
### Relationships
three or four short sentences
### Money
three or four short sentences
### Family and home
two or three short sentences
Under 280 words in total. No other headings."""


QA_FORECAST_RULES = STYLE_RULES + """

FORMAT (exactly)
**In short:** one or two plain sentences that answer the question.
### Most supportive times
Start with the stretches where both Jupiter and Saturn are supportive, then mention EVERY other stretch listed under SUPPORTIVE STRETCHES, each with exactly its dates. For each, one plain sentence saying why, using only the facts.
### Quieter times
One to three short items with dates, described only as quiet or neutral.
### Keep in mind
One or two plain sentences: these are tendencies from the classical texts, not predictions, and the books do not cover everything.
Under 300 words. No tables. No other headings."""


def report_prompt(kind, seeker, plain_facts, passages):
    fmt = MONTH_FORMAT if kind == "month" else YEAR_FORMAT
    unit = "month" if kind == "month" else "year"
    return f"""You are writing one {unit} of a personal guidance report for {seeker}.

{plain_facts}

CLASSICAL PASSAGES (the ONLY source for meaning; sources are named in brackets):
{passages}

{STYLE_RULES}

{fmt}

Address {seeker} as "you"."""


# ------------------------------------------------------------------ question answers (shown to ordinary users too)
QA_STYLE = """HOW TO WRITE (very important)
- Write for an ordinary adult who knows nothing about astrology, as a kind friend would explain it. Short sentences. Everyday words.
- Begin with "**In short:**" and one or two plain sentences that answer the question.
- The first time you use an astrology word (house, Lagna, dasha, transit, sub-period), explain it in a few plain words.
- Use "main period", "sub-period" and "short phase" for Mahadasha, Antardasha and Pratyantardasha, as the facts do.
- Never mention "tables", "supplied" or "provided" material; say "the classical texts" instead.
- Use soft words (may, tends to, a good time to). Never predict a specific event. Never call anything bad or unlucky.
- No remedies, gemstones, rituals or purchases."""


# ------------------------------------------------------------------ cleaning and checking the AI's text
_LEAD = re.compile(r"^\s*(?:period covered|the following (?:reading|guidance|forecast)|this (?:reading|guidance|forecast) covers|"
                   r"(?:month|year)\s+\d+\s+of)\b.*?(?:\n|$)", re.I)
_CITE = re.compile(r"\[\s*[\u2010-\u2015\-]?\s*(Brihat Jataka|Brihat Samhita|Phala Deepika|Cosmic 11 Nakshatra Notes)[^\]]*\]", re.I)


_NC_SENT = re.compile(r"\s*The books do not cover this area for this period\.?", re.I)


def soften(text):
    """Tendencies, not promises: 'will' becomes 'may' (book names in [brackets] are left alone)."""
    parts = re.split(r"(\[[^\]]*\])", text or "")
    out = []
    for part in parts:
        if part.startswith("["):
            out.append(part)
            continue
        part = re.sub(r"\bwon't\b", "may not", part, flags=re.I)
        part = re.sub(r"\b(W)ill\b", lambda m: "May", part)
        part = re.sub(r"\bwill\b", "may", part)
        out.append(part)
    return "".join(out)


def tidy_text(text):
    """Deterministic clean-up after the AI: no 'will', and never 'the books do not cover this' next to real content."""
    text = soften(text)
    pieces = re.split(r"(^#{2,4}\s*.+?\s*$)", text, flags=re.M)
    for i in range(0, len(pieces)):
        if pieces[i].startswith("#"):
            continue
        body = pieces[i]
        if _NC_SENT.search(body) and len(re.findall(r"[A-Za-z]{3,}", _NC_SENT.sub("", body))) > 2:
            pieces[i] = _NC_SENT.sub("", body).rstrip() + ("\n" if body.endswith("\n") else "")
    return re.sub(r"\n{3,}", "\n\n", "".join(pieces)).strip()


def clean_output(text):
    t = (text or "").replace("\u200b", "").strip()
    for _ in range(3):                                   # drop "Period covered..." style opening lines
        n = _LEAD.sub("", t, count=1)
        if n == t:
            break
        t = n.lstrip()
    t = _CITE.sub(lambda m: f"[{m.group(1)}]", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


_BANNED = [
    (r"\b\d{1,2}(?:st|nd|rd|th)[\s\u2010-\u2015-]*(?:house|sign)s?\b", "house numbers (such as '10th house')"),
    (r"\b(?:lagna|ascendant)\b", "the words Lagna/ascendant"),
    (r"\blords?\s+of\b|\bhouse\s+lord|\bruler of\b|\blordship\b", "house lords"),
    (r"\bfrom\s+(?:the|your)\s+(?:natal\s+)?(?:moon|lagna)\b", "'from the Moon/Lagna' counting"),
    (r"maha\s*-?dash?a|antar\s*-?dash?a|pratyantar\s*-?dash?a|bhukti|\bdasha\b", "Sanskrit period names (say main period, sub-period, short phase)"),
    (r"transit table|supplied|\bprovided\b|source material|\bpassages?\b|listed as", "wording that exposes how the answer was made"),
    (r"\b(?:unlikely|not likely|no chance|impossible)\b", "'unlikely' or similar (the facts never say this)"),
    (r"\b(?:strain|stress|tension|pressure|trouble|heavy feelings?)\b", "words about strain, stress or tension"),
    (r"\b(?:health|illness|ill|disease|sickness|accident|death)\b", "anything about health, illness, accidents or death"),
    (r"\brisk(?:y|s)?\b|\bavoid(?:ing)?\b", "warnings or advice to avoid things"),
    (r"retrograde|turns? (?:direct|backwards?)", "planets turning backwards"),
    (r"\b(?:should|must|best to|best held|held off|hold off|recommend(?:ed|s)?|advis(?:e|es|ed))\b", "advice or instructions"),
    (r"\b(?:loss|losses|lose|suffer\w*|miser(?:y|ies)|separat\w+|sons|fears?|danger|enem(?:y|ies)|disease\w*)\b", "hardships described in the books"),
]
NOT_COVERED = re.compile(r"The books do not cover this area(?: for this period)?\.?", re.I)


def _blocks(text):
    """[(heading, body)] for the ### sections of a text."""
    parts = re.split(r"^#{2,4}\s*(.+?)\s*$", text, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def lint_plain(text, kind="month"):
    """Style problems only (not accuracy). Empty list = fine."""
    issues = []
    for pat, label in _BANNED:
        if re.search(pat, text, re.I):
            issues.append(f"Do not use {label}. Rewrite that part in everyday words.")
    if not re.match(r"\s*\*\*\s*In short\s*:?\s*\*\*", text, re.I):
        issues.append("Start with **In short:** followed by one or two plain sentences.")
    want = {"month": ["Career and work", "Relationships", "Money"],
            "year": ["Career and work", "Relationships", "Money", "Family and home"],
            "qa": ["Most supportive times", "Quieter times", "Keep in mind"]}[kind]
    have = [h.strip() for h in re.findall(r"^#{2,4}\s*(.+?)\s*$", text, re.M)]
    if have != want:
        issues.append("Use exactly these headings, in this order, written as ### headings: " + ", ".join(want) + ".")
    limit = {"month": 230, "year": 380, "qa": 400}[kind]
    if len(re.findall(r"\w+", text)) > limit:
        issues.append(f"Too long. Keep it under {limit - 60} words.")
    for heading, body in _blocks(text):                              # content AND "the books do not cover this" in one area
        if NOT_COVERED.search(body):
            rest = NOT_COVERED.sub("", body)
            if len(re.findall(r"[A-Za-z]{3,}", rest)) > 2:
                issues.append(f"Under '{heading}' you wrote content and also said the books do not cover it. Keep only one: "
                              "use that sentence only when you have nothing else to say in that area.")
    return issues


def cited_sources(text, sources):
    """Sources to show under an answer: the books the text names in [brackets], plus Phala Deepika for the
    supportive / quiet verdicts (those come from its classical transit rules)."""
    tags = {t.strip() for t in re.findall(r"\[([^\]]+)\]", text or "") if t.strip()}
    have = sorted(set(sources))
    shown = {s_ for s_ in have for t in tags if s_.startswith(t) or t in s_}
    pd = next((s_ for s_ in have if s_.startswith("Phala Deepika")), "Phala Deepika (tr. V. Subrahmanya Sastri, 1937)")
    shown.add(pd)
    return sorted(shown)


def timeline_data(facts):
    """Lanes for the page's colour chart: Jupiter and Saturn (supportive / quiet) and the running sub-periods."""
    lo, hi = facts["window"]["start"], facts["window"]["end"]
    lanes = []
    for p in ("Jupiter", "Saturn"):
        if p in facts["transits"]:
            lanes.append({"key": p, "runs": [{"start": r["start"].isoformat(), "end": r["end"].isoformat(), "v": bool(r["v"]),
                                              "label": " / ".join(r["signs"])} for r in _runs(facts["transits"][p])]})
    per = [{"start": x["start"].isoformat(), "end": x["end"].isoformat(), "label": x["sub"] or x["main"]}
           for x in running_segments(facts, short=False)]
    if per:
        lanes.append({"key": "periods", "runs": per})
    return {"start": lo.isoformat(), "end": hi.isoformat(), "lanes": lanes}


# ------------------------------------------------------------------ key dates (calculated, shown at the top of a report)
def key_dates(facts, today=None):
    """Plain list of the dates that matter: sub-period changes, slow-planet moves, Sade Sati. Always engine-exact."""
    lo, hi = facts["window"]["start"], facts["window"]["end"]
    ev = []
    for md in facts["mahadashas"]:
        for ad in md["antardashas"]:
            if lo < ad["start"] <= hi:
                ev.append({"date": ad["start"], "kind": "sub_period", "planet": ad["lord"], "until": ad["end"]})
    for planet in ("Jupiter", "Saturn", "Rahu"):
        for seg in facts["transits"].get(planet, []):
            if lo < seg["start"] <= hi:
                ev.append({"date": seg["start"], "kind": "ingress", "planet": planet, "sign": seg["sign"],
                           "verdict": ("supportive" if seg.get("favourable") else "quiet") if seg.get("favourable") is not None else None})
    for sp in facts["sade_sati"]:
        if lo < sp["start"] <= hi:
            ev.append({"date": sp["start"], "kind": "sade_sati_start"})
        if lo < sp["end"] <= hi:
            ev.append({"date": sp["end"], "kind": "sade_sati_end"})
    ev.sort(key=lambda e: e["date"])
    return ev[:40]
