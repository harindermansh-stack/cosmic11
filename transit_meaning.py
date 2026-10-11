"""
transit_meaning.py - finds what the classical books actually say about each Jupiter and Saturn position.

Why: the plain-language facts hide house numbers, but the books are organised by house number ("When Saturn passes
through the 5th house from the Moon..."). With no way to match, the AI barely used the books and every month read the
same. Here the matching is done in code: for each (planet, house-from-Moon) the engine reports, we search the database,
pick the sentence that really describes that position, and hand it to the AI labelled by planet and SIGN
("Jupiter in Leo"). The AI turns the idea into plain words and is still forbidden to write house numbers.
"""
import re

import book_licenses as bl
from textscrub import scrub_text

WORD = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
        9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth"}
SLOW = ("Jupiter", "Saturn")


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def short_source(src):
    return re.sub(r"\s*\(.*$", "", src or "").strip()


def pairs_from(facts, planets=SLOW):
    """[(planet, sign, house_from_moon)] for every position the engine reports (positive length only)."""
    out = []
    for p in planets:
        for seg in facts["transits"].get(p, []):
            if seg["end"] > seg["start"]:
                k = (p, seg["sign"], seg["house_from_moon"])
                if k not in out:
                    out.append(k)
    return out


def find_meaning(docs, sources, planet, house):
    """The sentence that describes `planet` in the `house`th from the Moon -> (source, snippet) or None."""
    h = rf"(?:{house}(?:st|nd|rd|th)|{WORD[house]})"
    fwd = re.compile(rf"\b{planet}\b[^.;]{{0,90}}?\b{h}\b[^.;]{{0,60}}?\bMoon\b", re.I)
    rev = re.compile(rf"\b{h}\b[^.;]{{0,40}}?\bMoon\b[^.;]{{0,90}}?\b{planet}\b", re.I)
    for d, s in zip(docs, sources):
        cleaned, _ = scrub_text(d)
        if not cleaned:
            continue
        flat = re.sub(r"\s+", " ", cleaned)
        m = fwd.search(flat) or rev.search(flat)
        if not m:
            continue
        snippet = flat[m.start(): m.start() + 300]
        cut = max(snippet.rfind(". "), snippet.rfind("; "))
        if cut > 120:
            snippet = snippet[:cut + 1]
        return s, snippet.strip()
    return None


def fetch(col, facts, where=None, cache=None, planets=SLOW):
    """{(planet, sign): (short_source, snippet)}. `cache` (a dict) saves repeat searches within one report."""
    cache = cache if cache is not None else {}
    out = {}
    for planet, sign, house in pairs_from(facts, planets):
        key = (planet, house)
        if key not in cache:
            found = None
            try:
                kw = {"query_texts": [f"When {planet} passes through the {ordinal(house)} house from the Moon"], "n_results": 8}
                if where:
                    kw["where"] = where
                r = col.query(**kw)
                docs, srcs = bl.drop_excluded(list(r["documents"][0]), [m["source"] for m in r["metadatas"][0]])
                found = find_meaning(docs, srcs, planet, house)
            except Exception:
                found = None
            cache[key] = found
        if cache[key]:
            out[(planet, sign)] = (short_source(cache[key][0]), cache[key][1])
    return out
