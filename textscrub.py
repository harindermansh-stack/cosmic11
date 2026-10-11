"""
textscrub.py - removes OCR junk from the scanned books, word by word.

The scanned Phala Deepika carries Sanskrit verses and index pages that English OCR turns into gibberish
("aantaaad caaarst aaa: ara hraqzt ATATT"). That junk sits INSIDE lines of real English, so line-level cleaning is not
enough. This module compares every word with a list of real English words (english_words.txt.gz, a frequency-filtered
list from the MIT-licensed pyspellchecker package) plus astrology vocabulary, and removes:
  * words that are impossible in English (aaa, hraqzt, mixed-case noise, symbols inside words),
  * index references (XIV-3, VITL-12),
  * runs of three or more unknown words (garbled verses),
  * lines and chunks that are mostly junk.
It is used when the database is cleaned (clean_db_v2.py) and again on every passage before the AI sees it.
"""
import gzip
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_SOURCES = ("Cosmic 11 Nakshatra Notes",)

DOMAIN = set("""
sun moon mars mercury jupiter venus saturn rahu ketu aries taurus gemini cancer leo virgo libra scorpio sagittarius
capricorn aquarius pisces mesha vrishabha mithuna karka simha kanya tula vrischika dhanus makara kumbha meena
rasi rasis bhava bhavas lagna dasa dasas antardasa antardasas bhukti varga vargas kendra kendras trikona ashtaka
ashtakavarga bindus nakshatra nakshatras yoga yogas graha grahas sloka slokas adhyaya adhyayas adh brihat jataka samhita
varahamihira parasara mandala drekkana navamsa navamsas hora horas saptamsa dwadasamsa trimsamsa amsa amsas ghatika
ghatikas vimsottari vimshottari vaiseshikamsas upachaya malefic malefics benefic benefics exaltation debilitation
exalted debilitated ascendant aspected aspects conjunction conjunct retrograde combust moolatrikona
ashwini bharani krittika rohini mrigashira mrigasira ardra punarvasu pushya ashlesha aslesha magha purva uttara
phalguni hasta chitra swati vishakha visakha anuradha jyeshtha mula ashadha shravana sravana dhanishta shatabhisha
bhadrapada revati sade sati gochara vedha lakh crore horoscope horoscopes ascendants sidereal zodiacal zodiac
""".split())

ROMAN = re.compile(r"^[IVXLCDM]{1,8}$")
ORDINAL = re.compile(r"^(?:\d{1,2}(?:st|nd|rd|th)|[lI1][sS][tT]|[lI1]{1,2}[tT][hH]|[lI1][O0][tT][hH]|[1-9lI][0-9lIO]?[tT][hH])$", re.I)
REF = re.compile(r"^[A-Z0-9]{1,6}-[0-9lIOQ]{1,3}$")
SYMBOLS = re.compile(r"[|=~^<>_*\\\u00ac\u00b0\u00a6]")
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_VOCAB = None


def vocab():
    global _VOCAB
    if _VOCAB is None:
        words = set()
        p = os.path.join(_HERE, "english_words.txt.gz")
        if os.path.exists(p):
            with gzip.open(p, "rt", encoding="utf-8") as g:
                words = set(g.read().split())
        _VOCAB = words | DOMAIN
    return _VOCAB


def _core(tok):
    return re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", tok)


def _title(core):
    return len(core) >= 4 and core[0].isupper() and core[1:].islower()


def classify(tok):
    """'ok' | 'maybe' (unknown capitalised word, probably a name) | 'junk' | 'strong' (impossible, remove at once)"""
    core = _core(tok)
    if not core:
        return "strong" if SYMBOLS.search(tok) else "ok"
    if core.isdigit() or ROMAN.match(core) or ORDINAL.match(core):
        return "ok"
    if REF.match(core):
        return "strong"                                              # index reference such as XIV-3 or VITL-12
    inner = tok.strip(".,;:!?()[]\"' ")
    if SYMBOLS.search(inner) and len(core) >= 2:
        return "strong"                                              # symbols inside a word: ooto^HaN
    if "-" in core:
        parts = [p for p in core.split("-") if p]
        kinds = [classify(p) for p in parts]
        if "strong" in kinds:
            return "strong"
        return "ok" if all(k in ("ok", "maybe") for k in kinds) else "junk"
    if re.search(r"[A-Za-z]\d|\d[A-Za-z]", core) and not ORDINAL.match(core):
        return "strong" if len(core) >= 5 else "junk"                # letters mixed with digits: X1V, w2OiS
    low = core.lower()
    if len(core) >= 2 and core.isupper() and low not in vocab():
        return "strong"                                              # unknown shouted word: NXVITLL, XALIL, ATATT
    if len(core) >= 3 and re.search(r"aaa|eee|iii|ooo|uuu", low):
        return "strong"
    if len(core) >= 4 and re.search(r"q(?!u)", low):
        return "strong"
    flips = sum(1 for a, b in zip(core, core[1:]) if a.islower() and b.isupper())
    if flips >= 2:
        return "strong"                                              # aRTIw
    if low in vocab():
        return "ok"
    if len(core) == 1:
        return "ok" if core in "aAI" else "junk"
    if _title(core):
        return "maybe"
    return "junk"


def scrub_line(line):
    """-> (cleaned_line, removed_tokens, total_tokens)"""
    toks = line.split()
    kinds = [classify(t) for t in toks]
    drop = [k == "strong" for k in kinds]
    i, n = 0, len(toks)
    while i < n:                                                    # runs of 3+ unknown words (garbled verses)
        if kinds[i] in ("junk", "maybe", "strong"):
            j = i
            while j < n and kinds[j] in ("junk", "maybe", "strong"):
                j += 1
            definite = sum(1 for k in kinds[i:j] if k in ("junk", "strong"))
            if j - i >= 3 and definite >= 2:
                for k in range(i, j):
                    drop[k] = True
            i = j
        else:
            i += 1
    caps = [t for t in range(n) if re.fullmatch(r"[A-Z]{3,}", _core(toks[t])) and kinds[t] != "ok"]   # shouted unknown caps
    for a, b in zip(caps, caps[1:]):
        if b == a + 1:
            drop[a] = drop[b] = True
    kept = [t for t, d in zip(toks, drop) if not d]
    removed = sum(drop)
    if removed >= 2 and len(kept) >= 4:                              # junk removed and only tiny words left: tea fe wae vat git
        tiny = sum(1 for t in kept if len(_core(t)) <= 3)
        if tiny / len(kept) > 0.6 and len(kept) < 16:
            return "", n, n
    return " ".join(kept), removed, n


def english_ratio(text):
    toks = [_core(t) for t in text.split()]
    toks = [t for t in toks if len(t) >= 2 and t.isalpha()]
    if not toks:
        return 0.0
    score = 0.0
    for t in toks:
        k = classify(t)
        score += 1.0 if k == "ok" else (0.5 if k == "maybe" else 0.0)
    return score / len(toks)


def scrub_text(text, min_words=20, min_ratio=0.60):
    """-> (cleaned_text or None, info). None means the whole passage is junk."""
    text = CTRL.sub("", (text or "").replace("\ufffd", " "))
    out, removed, total = [], 0, 0
    refs = sum(1 for t in text.split() if REF.match(_core(t)))      # index references (XIV-3) mark an index page
    for ln in text.splitlines():
        if not ln.strip():
            continue
        cl, r, n = scrub_line(ln)
        removed += r
        total += n
        if n >= 4 and (r / n >= 0.5 or len(cl.split()) < 3):
            removed += n - r                                         # a line that is mostly junk goes entirely
            continue
        if cl.strip():
            out.append(cl)
    cleaned = "\n".join(out).strip()
    words = len([t for t in cleaned.split() if any(c.isalpha() for c in t)])
    ratio = english_ratio(cleaned)
    info = {"removed": removed, "total": total, "words": words, "ratio": round(ratio, 2), "refs": refs}
    if refs >= 3 and refs / max(total, 1) >= 0.05:
        return None, info                                            # an index page
    if words < min_words or ratio < min_ratio:
        return None, info
    return cleaned, info


def filter_passages(docs, sources):
    """Scrub every retrieved passage; drop the ones that are mostly junk. Keeps order. Our own notes are untouched."""
    out_d, out_s = [], []
    for d, s in zip(docs, sources):
        if str(s).startswith(SKIP_SOURCES):
            out_d.append(d); out_s.append(s)
            continue
        cleaned, _ = scrub_text(d)
        if cleaned:
            out_d.append(cleaned); out_s.append(s)
    return out_d, out_s
