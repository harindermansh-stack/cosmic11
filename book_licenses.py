"""
book_licenses.py - one place that decides what the app may SHOW or QUOTE from each book.

status values (edit BOOK_STATUS below after checking each book, see the licensing plan):
  "public_domain" : passages may be shown, and the AI may quote one short phrase
  "licensed"      : you hold written permission; passages may be shown and quoted as the licence allows
  "unverified"    : DEFAULT. Used privately to find relevant rules. The AI must PARAPHRASE and never quote;
                    users never see the raw text
  "excluded"      : never used at all

PUBLIC_MODE is ON by default. Set the environment variable PUBLIC_MODE=0 only on your own laptop
for testing (it shows everything, as before). Never deploy with PUBLIC_MODE=0.
"""
import os

PUBLIC_MODE = os.getenv("PUBLIC_MODE", "1") != "0"

BOOK_STATUS = {
    # --- books in the new astrology database (corpus_v2) ---
    "Brihat Jataka": "public_domain",           # tr. N. Chidambaram Iyer, 1885
    "Brihat Samhita": "public_domain",          # tr. N. Chidambaram Iyer, 1884
    "Phala Deepika": "unverified",              # tr. V. Subrahmanya Sastri, 1937: public domain in India, not yet in the US -> paraphrase only
    "Cosmic 11 Nakshatra Notes": "own_work",    # written in our own words
    # --- old books: kept for private study on the laptop, never used in the public app ---
    "BPHS Vol 1": "excluded",
    "Lal Kitab": "excluded",
    "Nakshatras": "excluded",
    "Vastu for House": "excluded",
    "Body Reading": "excluded",
    "Cheiro Numbers": "excluded",
    "Cheiro Palmistry": "excluded",
}
SHOWABLE = ("public_domain", "licensed", "own_work")

QUOTE_RULES = """

QUOTATION RULES (copyright)
- Put every rule in your own words. Do not use quotation marks anywhere in the answer.
- Do not copy, or closely follow the wording of, any passage.
- Cite by book name only, and only for statements that come from a passage of that book."""


def public_mode():
    return PUBLIC_MODE


def status_of(source):
    s = str(source).lower()
    for name, st in BOOK_STATUS.items():
        if name.lower() in s or s in name.lower():
            return st
    return "unverified"


def drop_excluded(docs, sources):
    if not public_mode():          # on your own laptop (PUBLIC_MODE=0) every book stays available for study
        return list(docs), list(sources)
    keep = [(d, s) for d, s in zip(docs, sources) if status_of(s) != "excluded"]
    return [d for d, _ in keep], [s for _, s in keep]


def tag_passages(docs, sources):
    lines = []
    for d, s in zip(docs, sources):
        tag = ""
        if public_mode():
            st = status_of(s)
            tag = (" [PUBLIC DOMAIN]" if st in ("public_domain", "own_work")
                   else " [LICENSED: quote only as your licence allows]" if st == "licensed"
                   else " [PARAPHRASE ONLY: never quote]")
        lines.append(f"{s}{tag}: {d}")
    return "\n".join(lines)


def with_quote_rules(prompt):
    return prompt + QUOTE_RULES if public_mode() else prompt


def display_passages(docs, sources):
    out = []
    for d, s in zip(docs, sources):
        show = (not public_mode()) or status_of(s) in SHOWABLE
        out.append({"source": s, "text": d if show else "(text not shown: this book is used for paraphrase only)"})
    return out
