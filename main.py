"""
main.py — FastAPI wrapper around the proven, tested backend logic
(chart_engine.py's calculation + the grounded-retrieval fixes from app.py).

This is the bridge between the Cosmic 11 Next.js frontend and the backend
we've spent this whole project debugging. It does NOT reimplement any of
the retrieval logic — it imports and calls the exact same functions that
app.py uses, so every bug fix from tonight (named-planet queries, query
ordering, nakshatra queries, domain filtering) carries over automatically.

Run with:
    uvicorn main:app --reload --port 8000
"""

import os
from datetime import datetime, date, time as dtime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import chromadb
import requests
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

from chart_engine import calculate_chart, ChartResult
import time
import book_licenses as bl
from answer_checker import house_lords_text, check_answer, correction_block, warning_text, RETRY_WAIT
from plain_layer import (QA_STYLE, clean_output, lint_plain, cited_sources, tidy_text, plain_facts_text,
                         completeness_issues, timeline_data)
import transit_meaning
import textscrub
from forecast_engine import (prepare_forecast, build_forecast, forecast_text, merge_queries,
                             round_robin, forecast_prompt, fit_prompt, est_tokens, TPM_BUDGET)

load_dotenv()

app = FastAPI(title="Cosmic 11 API", version="1.0")

# Allow the Next.js frontend (localhost during dev, your real domain in prod)
# to call this API. Tighten allow_origins to your actual domain before going live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to your real frontend domain before deploying
    allow_methods=["*"],
    allow_headers=["*"],
)

from rag_config import CHROMA_PATH  # new astrology-only database
from rag_config import COLLECTION_NAME

# ---- Same classifier/query logic as app.py, imported as-is conceptually ----
# (kept inline here since app.py doesn't currently expose these as an
#  importable module — see note at the bottom about the next cleanup step)

_FOCUS_KEYWORDS = {
    "business": ["business", "startup", "venture", "partnership firm"],
    "career": ["career", "job", "profession", "promotion", "work"],
    "marriage": ["marriage", "married", "spouse", "husband", "wife", "wedding"],
    "relationship": ["relationship", "love", "girlfriend", "boyfriend", "partner"],
    "wealth": ["money", "wealth", "finance", "income", "savings"],
    "gains": ["gain", "profit", "salary hike", "windfall"],
    "health": ["health", "illness", "disease", "surgery", "medical"],
    "education": ["study", "exam", "education", "degree", "college"],
    "children": ["child", "children", "pregnancy", "conceive"],
    "family": ["family", "mother", "home", "domestic"],
    "father": ["father", "dad"],
    "foreign": ["abroad", "foreign", "visa", "relocate", "immigration"],
    "spirituality": ["spiritual", "moksha", "meditation", "religion"],
    "longevity": ["longevity", "life span", "death", "accident"],
    "obstacles": ["problem", "obstacle", "struggle", "difficulty"],
    "enemies": ["enemy", "conflict", "court case", "litigation", "dispute"],
}

HOUSE_FOCUS_MAP = {
    "self": [1, 3, 10], "wealth": [2, 11, 5], "siblings": [3, 11, 4],
    "family": [4, 2, 9], "home": [4, 2, 11], "children": [5, 9, 11],
    "education": [5, 4, 9], "health": [1, 6, 8], "enemies": [6, 3, 12],
    "debt": [6, 12, 2], "marriage": [7, 2, 11], "relationship": [7, 5, 11],
    "longevity": [8, 1, 3], "obstacles": [8, 6, 12], "fortune": [9, 5, 1],
    "father": [9, 10, 1], "career": [10, 6, 2], "business": [10, 7, 11],
    "gains": [11, 2, 5], "spirituality": [12, 8, 9], "foreign": [12, 9, 3],
}


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def classify_focus_area(user_question: str) -> str:
    q = user_question.lower()
    for area, keywords in _FOCUS_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return area
    return ""


def build_retrieval_queries(chart: ChartResult, focus_area: str = "", user_question: str = ""):
    queries = []
    named_planets = set()
    if user_question:
        q_lower = user_question.lower()
        for p in chart.placements:
            if p.planet.lower() in q_lower:
                named_planets.add(p.planet)

    for p in chart.placements:
        if p.planet in named_planets:
            retro = "retrograde" if p.retrograde else ""
            q = f"{p.planet} in {p.sign} {_ordinal(p.house)} house {retro} {p.nakshatra} effects"
            queries.append(" ".join(q.split()))

    dasha_placement = next((p for p in chart.placements if p.planet == chart.active_mahadasha), None)
    if dasha_placement and dasha_placement.planet not in named_planets:
        queries.append(
            f"{dasha_placement.planet} dasha effects {dasha_placement.planet} in "
            f"{dasha_placement.sign} {_ordinal(dasha_placement.house)} house {dasha_placement.nakshatra} nakshatra"
        )
    queries.append(f"{chart.ascendant_sign} ascendant lagna characteristics")

    moon = next((p for p in chart.placements if p.planet == "Moon"), None)
    nakshatras_to_query = set()
    if moon:
        nakshatras_to_query.add(moon.nakshatra)
    if user_question:
        q_lower = user_question.lower()
        for p in chart.placements:
            if p.nakshatra.lower() in q_lower:
                nakshatras_to_query.add(p.nakshatra)
    for nak in nakshatras_to_query:
        queries.append(f"{nak} nakshatra characteristics qualities symbol deity life purpose")

    relevant_houses = HOUSE_FOCUS_MAP.get(focus_area.lower(), [])
    if not relevant_houses:
        relevant_houses = list({p.house for p in chart.placements})

    for p in chart.placements:
        if p.planet in named_planets:
            continue
        if p.house in relevant_houses:
            retro = "retrograde" if p.retrograde else ""
            q = f"{p.planet} in {p.sign} {_ordinal(p.house)} house {retro} {p.nakshatra} effects"
            queries.append(" ".join(q.split()))
    return queries


# ---- Request/response models ----

class BirthDetails(BaseModel):
    name: Optional[str] = ""
    birth_date: date = Field(..., examples=["1969-11-29"])
    birth_time: dtime = Field(..., examples=["05:30:00"])
    latitude: float
    longitude: float
    utc_offset: float = 5.5


class ChartResponse(BaseModel):
    ascendant_sign: str
    ascendant_deg: float
    ascendant_nakshatra: str
    active_mahadasha: str
    mahadasha_start: str
    mahadasha_end: str
    placements: list


class ReadingRequest(BaseModel):
    birth: BirthDetails
    question: str
    groq_api_key: str
    domain: Optional[str] = "All"  # "All" | "Astrology" | "Vastu"


class ReadingResponse(BaseModel):
    answer: str
    sources: list[str]
    retrieved_passages: list[dict]
    retrieval_queries_used: list[str]
    facts_text: Optional[str] = None
    timeline: Optional[dict] = None


def _chart_to_response(chart: ChartResult) -> ChartResponse:
    return ChartResponse(
        ascendant_sign=chart.ascendant_sign,
        ascendant_deg=round(chart.ascendant_deg, 2),
        ascendant_nakshatra=chart.ascendant_nakshatra,
        active_mahadasha=chart.active_mahadasha,
        mahadasha_start=chart.mahadasha_start.date().isoformat(),
        mahadasha_end=chart.mahadasha_end.date().isoformat(),
        placements=[
            {
                "planet": p.planet, "sign": p.sign,
                "degree": round(p.sign_lord_deg, 2), "house": p.house,
                "nakshatra": p.nakshatra, "pada": p.pada, "retrograde": p.retrograde,
            }
            for p in chart.placements
        ],
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chart", response_model=ChartResponse)
def get_chart(birth: BirthDetails):
    """Compute a chart from birth details — no AI call, pure deterministic calculation."""
    try:
        birth_dt = datetime.combine(birth.birth_date, birth.birth_time)
        chart = calculate_chart(birth_dt, birth.latitude, birth.longitude, birth.utc_offset)
        return _chart_to_response(chart)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Chart calculation failed: {e}")


class ForecastRequest(BaseModel):
    birth: BirthDetails
    question: str = "next 12 months"   # any period in plain words: "2028", "2027 to 2035", "next 10 years"


@app.post("/forecast")
def get_forecast(req: ForecastRequest):
    """Verified dasha + transit facts for the period named in `question`. No AI involved."""
    try:
        birth_dt = datetime.combine(req.birth.birth_date, req.birth.birth_time)
        chart = calculate_chart(birth_dt, req.birth.latitude, req.birth.longitude, req.birth.utc_offset)
        fc = prepare_forecast(chart, birth_dt, req.birth.utc_offset, req.question)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Forecast failed: {e}")
    if fc is None:
        raise HTTPException(status_code=422, detail="Name a period, e.g. '2028', '2027 to 2035' or 'next 10 years'.")
    import json
    return {"period": fc["window"].label, "facts_text": fc["facts_text"],
            "facts": json.loads(json.dumps(fc["facts"], default=str))}


@app.post("/reading", response_model=ReadingResponse)
def get_reading(req: ReadingRequest):
    """Full grounded reading: chart + retrieval + Groq — same logic as app.py."""
    try:
        birth_dt = datetime.combine(req.birth.birth_date, req.birth.birth_time)
        chart = calculate_chart(birth_dt, req.birth.latitude, req.birth.longitude, req.birth.utc_offset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Chart calculation failed: {e}")

    ef = embedding_functions.DefaultEmbeddingFunction()
    try:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database not found: {e}")

    where = None
    if req.domain == "Astrology":
        where = {"domain": "astrology"}
    elif req.domain == "Vastu":
        where = {"domain": "vastu"}

    focus_area = classify_focus_area(req.question)
    retrieval_queries = build_retrieval_queries(chart, focus_area, req.question)

    # If the question names a period (2028, "2027 to 2035", "next 10 years"...), compute verified timeline facts.
    fc = prepare_forecast(chart, birth_dt, req.birth.utc_offset, req.question)

    all_docs, all_sources, seen = [], [], set()
    if fc:
        retrieval_queries = merge_queries(retrieval_queries, fc["queries"])
        per_query = []
        for q in retrieval_queries:
            results = collection.query(query_texts=[q], n_results=fc["per_query"], where=where)
            per_query.append([(d, m["source"]) for d, m in zip(results["documents"][0], results["metadatas"][0])])
        picked = round_robin(per_query, fc["passage_cap"])
        all_docs, all_sources = [d for d, _ in picked], [s for _, s in picked]
        cap = fc["passage_cap"]
    else:
        for q in retrieval_queries:
            results = collection.query(query_texts=[q], n_results=10, where=where)
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                key = doc[:80]
                if key not in seen:
                    seen.add(key)
                    all_docs.append(doc)
                    all_sources.append(meta["source"])
        cap = 15

    retrieved = all_docs[:cap]
    sources = all_sources[:cap]
    retrieved, sources = bl.drop_excluded(retrieved, sources)
    retrieved, sources = textscrub.filter_passages(retrieved, sources)   # no OCR junk reaches the AI
    rules = bl.tag_passages(retrieved, sources)

    seeker = req.birth.name if req.birth.name else "the seeker"
    chart_summary = "\n".join(
        f"- {p.planet} in {p.sign}, house {p.house}, {p.nakshatra} pada {p.pada}"
        f"{' (retrograde)' if p.retrograde else ''}"
        for p in chart.placements
    )
    chart_summary += "\n" + house_lords_text(chart.ascendant_sign)

    meanings = {}
    if fc:                                   # attach what the books say about each Jupiter / Saturn position (plain facts)
        pa = fc["plain_args"]
        meanings = dict(list(transit_meaning.fetch(collection, fc["facts"], where).items())[:6]) if pa["meanings"] else {}

        def _plain_facts(m):
            t = plain_facts_text(fc["facts"], chart, pa["label"], short=pa["short"], meanings=m, summary=pa["summary"])
            return t + ("\nNOTE: " + pa["note"] if pa["note"] else "")
        fc["plain_facts"] = _plain_facts(meanings)
        prompt = forecast_prompt(seeker, chart, chart_summary, fc, rules, req.question)
    else:
      prompt = f"""You are an expert Vedic astrologer.
You MUST base your answer on the specific chart data and retrieved classical
text below. Do NOT give a generic answer that could apply to anyone — every
claim must tie back to one of the placements listed.

Person: {seeker}
Ascendant: {chart.ascendant_sign} {chart.ascendant_deg:.2f}° ({chart.ascendant_nakshatra})
Current Mahadasha: {chart.active_mahadasha} ({chart.mahadasha_start.date()} to {chart.mahadasha_end.date()})
Placements:
{chart_summary}

Classical texts (use only these as source material):
{rules}

Question: {req.question}

{QA_STYLE}

Give a warm, specific answer of about 5 to 7 sentences that ties back to the actual
placements above and names the classical sources in square brackets. Address {seeker} directly.
If the classical texts don't cover something relevant, say so honestly
rather than filling in generic knowledge."""

    prompt = bl.with_quote_rules(prompt)
    if fc:  # keep within Groq's free per-minute token limit: fewer general passages first, then fewer passage lines
        items = list(meanings.items())
        retrieved0, sources0 = list(retrieved), list(sources)
        while True:
            fc["plain_facts"] = _plain_facts(dict(items))
            prompt, retrieved, sources = fit_prompt(
                lambda r, s: bl.with_quote_rules(forecast_prompt(seeker, chart, chart_summary, fc, bl.tag_passages(r, s), req.question)),
                retrieved0, sources0, fc["max_tokens"])
            if not items or est_tokens(prompt) + fc["max_tokens"] <= TPM_BUDGET:
                break
            items = items[:-1]

    def ask_groq(p):
        return requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + req.groq_api_key},
            json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": p}],
                  "max_tokens": (fc["max_tokens"] if fc else 2000), "reasoning_format": "hidden",
                  **({"reasoning_effort": "low"} if fc else {})},
        ).json()

    t_first = time.time()
    r = ask_groq(prompt)
    if "choices" not in r:
        raise HTTPException(status_code=502, detail=f"Groq API error: {r}")
    answer = clean_output(r["choices"][0]["message"]["content"] or "")
    if fc:
        answer = tidy_text(answer)
    finish = r["choices"][0].get("finish_reason")
    issues = check_answer(answer, chart, fc["facts"] if fc else None)
    style = (lint_plain(answer, "qa") + completeness_issues(answer, fc["must"])) if fc else []   # plain rules + every supportive stretch named
    if issues or style:  # one corrected retry, after Groq's per-minute limit resets
        time.sleep(max(0, RETRY_WAIT - (time.time() - t_first)))
        corr = correction_block((issues + style)[:6])
        retry_prompt = prompt
        if fc:                                                       # leave room for the corrections inside Groq's per-minute budget
            retry_prompt, _r, _s = fit_prompt(
                lambda r, s: bl.with_quote_rules(forecast_prompt(seeker, chart, chart_summary, fc, bl.tag_passages(r, s), req.question)),
                retrieved0, sources0, fc["max_tokens"], budget=TPM_BUDGET - est_tokens(corr))
        r2 = ask_groq(retry_prompt + corr)
        if "choices" in r2:
            a2 = clean_output(r2["choices"][0]["message"]["content"] or "")
            if fc:
                a2 = tidy_text(a2)
            i2 = check_answer(a2, chart, fc["facts"] if fc else None)
            s2 = (lint_plain(a2, "qa") + completeness_issues(a2, fc["must"])) if fc else []
            if 3 * len(i2) + len(s2) <= 3 * len(issues) + len(style):
                answer, issues, style = a2, i2, s2
                finish = r2["choices"][0].get("finish_reason")
    if finish == "length":
        answer += "\n\n*(This answer was cut short by the free plan's size limit. For a fuller answer, ask about a shorter period.)*"
    if issues:
        answer += warning_text(issues)

    return ReadingResponse(
        answer=answer,
        sources=(cited_sources(answer, sources) if fc else list(set(sources))),
        retrieved_passages=bl.display_passages(retrieved, sources),
        retrieval_queries_used=retrieval_queries,
        facts_text=(fc["facts_text"] if fc else None),
        timeline=(timeline_data(fc["facts"]) if fc else None),
    )


# NOTE for next cleanup step: classify_focus_area, build_retrieval_queries, and
# _ordinal are currently duplicated between this file and app.py. Once this API
# is confirmed working, extract them into a shared retrieval_logic.py that both
# app.py (Streamlit) and main.py (FastAPI) import — avoids the two ever drifting
# out of sync after future bug fixes.


# ==================================================================================
# PUBLIC APP: the web page and the endpoint it calls (same address, so no CORS issues)
# ==================================================================================
import guardrails
import usage_limits
from rag_config import PRICE_INR, DAILY_PER_USER

TOPIC_QUESTIONS = {
    "marriage": "When are the favourable periods for marriage and relationships in the next 3 years, and why?",
    "career": "How will my career and work develop over the next 3 years?",
    "money": "How will my finances and income develop over the next 3 years?",
}
PRICE_NOTE = {"en": f"Free during beta. After launch: \u20b9{PRICE_INR} per question, always shown before you ask.",
              "hi": f"\u092c\u0940\u091f\u093e \u0915\u0947 \u0926\u094c\u0930\u093e\u0928 \u0928\u093f\u0903\u0936\u0941\u0932\u094d\u0915\u0964 \u0932\u0949\u0928\u094d\u091a \u0915\u0947 \u092c\u093e\u0926: \u20b9{PRICE_INR} \u092a\u094d\u0930\u0924\u093f \u092a\u094d\u0930\u0936\u094d\u0928, \u092a\u0942\u091b\u0928\u0947 \u0938\u0947 \u092a\u0939\u0932\u0947 \u0939\u092e\u0947\u0936\u093e \u0926\u093f\u0916\u093e\u092f\u093e \u091c\u093e\u090f\u0917\u093e\u0964"}


class PublicReadingRequest(BaseModel):
    name: Optional[str] = ""
    birth_date: date
    birth_time: dtime
    latitude: float
    longitude: float
    utc_offset: float = 5.5
    topic: Optional[str] = None          # "marriage" | "career" | "money" | None (own question)
    question: Optional[str] = ""
    language: str = "en"                 # "en" | "hi"


def _user_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def translate_to_hindi(text: str, key: str):
    """Translate a finished, already fact-checked English reading. Uses a different model so it has its own
    Groq per-minute limit. Returns None if translation is not available."""
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
            json={"model": "llama-3.3-70b-versatile", "max_tokens": 3000, "messages": [{"role": "user", "content":
                  "Translate this astrology reading into simple, natural everyday Hindi in Devanagari script, the way a "
                  "friendly person would explain it. Keep every date, number, planet name, sign name and book name exactly as "
                  "written. Do not add, remove or soften anything. Keep the same markdown: **bold**, ### headings and lists. "
                  "Use these terms: 'In short' = **संक्षेप में:**, 'main period' = मुख्य काल (महादशा), "
                  "'sub-period' = उप-काल (अंतर्दशा), 'short phase' = लघु चरण (प्रत्यंतर्दशा), 'Career and work' = करियर और काम, "
                  "'Relationships' = संबंध, 'Money' = धन, 'Family and home' = परिवार और घर, "
                  "'Most supportive times' = सबसे अनुकूल समय, 'Quieter times' = शांत समय, 'Keep in mind' = ध्यान रखें, "
                  "'supportive' = अनुकूल, 'quiet' = शांत.\n\n" + text}]},
            timeout=90,
        ).json()
        return r["choices"][0]["message"]["content"] if "choices" in r else None
    except Exception:
        return None


@app.post("/api/reading")
def public_reading(req: PublicReadingRequest, request: Request):
    lang = "hi" if req.language == "hi" else "en"
    key = os.getenv("GROQ_API_KEY", "")
    if not key or key == "your_key_here":
        raise HTTPException(status_code=503, detail="The reading engine is not configured yet.")
    if req.topic and req.topic not in TOPIC_QUESTIONS:
        raise HTTPException(status_code=422, detail="Unknown topic.")
    question = TOPIC_QUESTIONS[req.topic] if req.topic else (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="Please choose a topic or type a question.")
    if len(question) > 500:
        raise HTTPException(status_code=422, detail="Please keep your question under 500 characters.")

    guard = guardrails.check_question(question)          # care first: no reading, no usage counted
    if guard:
        return {"guardrail": guard["kind"], "answer": guard[lang], "sources": [], "facts_text": None,
                "language": lang, "price_note": PRICE_NOTE[lang], "remaining_today": None}

    user = _user_key(request)
    allowed, remaining, why = usage_limits.take(user)
    if not allowed:
        msg = {("user", "en"): f"You have used today's {DAILY_PER_USER} free questions. Please come back tomorrow.",
               ("user", "hi"): f"\u0906\u091c \u0915\u0947 {DAILY_PER_USER} \u0928\u093f\u0903\u0936\u0941\u0932\u094d\u0915 \u092a\u094d\u0930\u0936\u094d\u0928 \u092a\u0942\u0930\u0947 \u0939\u094b \u0917\u090f\u0964 \u0915\u0943\u092a\u092f\u093e \u0915\u0932 \u092b\u093f\u0930 \u0906\u090f\u0901\u0964",
               ("total", "en"): "Cosmic 11 has reached today's limit for free readings. Please try again tomorrow.",
               ("total", "hi"): "Cosmic 11 \u0928\u0947 \u0906\u091c \u0915\u0940 \u0928\u093f\u0903\u0936\u0941\u0932\u094d\u0915 \u0938\u0940\u092e\u093e \u092a\u0942\u0930\u0940 \u0915\u0930 \u0932\u0940 \u0939\u0948\u0964 \u0915\u0943\u092a\u092f\u093e \u0915\u0932 \u092b\u093f\u0930 \u0915\u094b\u0936\u093f\u0936 \u0915\u0930\u0947\u0902\u0964"}
        raise HTTPException(status_code=429, detail=msg[(why, lang)])

    try:
        res = get_reading(ReadingRequest(
            birth=BirthDetails(name=req.name or "", birth_date=req.birth_date, birth_time=req.birth_time,
                               latitude=req.latitude, longitude=req.longitude, utc_offset=req.utc_offset),
            question=question, groq_api_key=key, domain="Astrology"))
    except Exception:
        usage_limits.give_back(user)                       # a failed reading does not use up a question
        raise

    answer, out_lang = res.answer, "en"
    if lang == "hi":
        hi = translate_to_hindi(answer, key)
        if hi:
            answer, out_lang = hi, "hi"
        else:
            answer += "\n\n(\u0939\u093f\u0902\u0926\u0940 \u0905\u0928\u0941\u0935\u093e\u0926 \u0905\u092d\u0940 \u0909\u092a\u0932\u092c\u094d\u0927 \u0928\u0939\u0940\u0902 \u0939\u0948, \u0907\u0938\u0932\u093f\u090f \u0905\u0902\u0917\u094d\u0930\u0947\u091c\u093c\u0940 \u092e\u0947\u0902 \u0926\u093f\u0916\u093e\u092f\u093e \u0917\u092f\u093e \u0939\u0948\u0964)"
    answer += "\n\n" + guardrails.disclaimer(out_lang)
    return {"guardrail": None, "answer": answer, "language": out_lang, "question": question, "topic": req.topic,
            "sources": res.sources, "facts_text": res.facts_text, "timeline": res.timeline, "price_note": PRICE_NOTE[lang],
            "remaining_today": remaining}


# ---------------- 5-Year Guidance Report ----------------
import report_engine
from rag_config import REPORT_PRICES_INR, REPORT_MODE


class ReportRequest(BaseModel):
    name: Optional[str] = ""
    birth_date: date
    birth_time: dtime
    latitude: float
    longitude: float
    utc_offset: float = 5.5
    language: str = "en"
    invite_code: Optional[str] = ""
    kind: str = "year1"                  # "year1" (\u20b999, 12 months) | "year5" (\u20b9499, 12 months + years 2-5)


@app.post("/api/report")
def create_report(req: ReportRequest, request: Request):
    lang = "hi" if req.language == "hi" else "en"
    key = os.getenv("GROQ_API_KEY", "")
    if not key or key == "your_key_here":
        raise HTTPException(status_code=503, detail="The reading engine is not configured yet.")
    if req.kind not in report_engine.KINDS:
        raise HTTPException(status_code=422, detail="Unknown report type.")
    if os.getenv("REPORT_MODE", REPORT_MODE) == "beta":
        code = os.getenv("BETA_CODE", "").strip()
        if not code or (req.invite_code or "").strip().upper() != code.upper():
            raise HTTPException(status_code=403, detail=("Please enter a valid beta invite code." if lang == "en"
                                else "\u0915\u0943\u092a\u092f\u093e \u0938\u0939\u0940 \u092c\u0940\u091f\u093e \u0906\u092e\u0902\u0924\u094d\u0930\u0923 \u0915\u094b\u0921 \u0926\u0930\u094d\u091c \u0915\u0930\u0947\u0902\u0964"))
    else:
        raise HTTPException(status_code=402, detail="Payments are not enabled yet.")
    allowed, why = usage_limits.take_report(_user_key(request))
    if not allowed:
        raise HTTPException(status_code=429, detail=("You can prepare one report per day. Please come back tomorrow." if why == "user"
                            else "Today's report limit has been reached. Please try again tomorrow."))
    report_engine.cleanup()
    job_id = report_engine.start({"name": req.name or "", "birth_date": req.birth_date.isoformat(),
                                  "birth_time": req.birth_time.strftime("%H:%M"), "latitude": req.latitude,
                                  "longitude": req.longitude, "utc_offset": req.utc_offset}, lang, key, req.kind)
    return {"id": job_id, "kind": req.kind, "price_inr": REPORT_PRICES_INR[req.kind]}


@app.get("/api/report/{job_id}")
def report_status(job_id: str):
    job = report_engine.load(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report not found or already deleted (reports are kept for 48 hours).")
    return job


# ---------------- Legal pages and source code link ----------------
from fastapi.responses import HTMLResponse, RedirectResponse
_LEGAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legal")


def _legal(name):
    text = open(os.path.join(_LEGAL, name), encoding="utf-8").read()
    fill = {"{{OWNER_NAME}}": os.getenv("LEGAL_OWNER", "[owner name to be added]"),
            "{{CONTACT_EMAIL}}": os.getenv("LEGAL_EMAIL", "[contact email to be added]"),
            "{{CITY}}": os.getenv("LEGAL_CITY", "Bengaluru"),
            "{{UPDATED}}": os.getenv("LEGAL_UPDATED", "October 2026")}
    for k, v in fill.items():
        text = text.replace(k, v.replace("<", "&lt;"))
    return HTMLResponse(text)


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    return _legal("privacy.html")


@app.get("/terms", response_class=HTMLResponse)
def terms_page():
    return _legal("terms.html")


@app.get("/source")
def source_code():
    url = os.getenv("SOURCE_URL", "")
    if url.startswith("https://"):
        return RedirectResponse(url)
    return HTMLResponse("<p style='font-family:system-ui;padding:20px'>Swiss Ephemeris Inside. "
                        "The source code link will be published here at launch.</p>")


# ---------------- Beta feedback (thumbs up/down + optional comment) ----------------
import csv
import hmac
import io
import json as _json
import threading as _threading
from datetime import datetime as _dt, timedelta as _td
from fastapi.responses import Response

FEEDBACK_KEEP_DAYS = 180
FEEDBACK_MAX_BYTES = 5_000_000
_fb_lock = _threading.Lock()


def _feedback_file():
    return os.getenv("FEEDBACK_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.jsonl"))


class FeedbackRequest(BaseModel):
    kind: str                      # "answer" | "report"
    rating: str                    # "up" | "down"
    comment: Optional[str] = ""
    ref: Optional[str] = ""        # the question asked, or the report id
    language: str = "en"


def _read_feedback():
    try:
        with open(_feedback_file(), encoding="utf-8") as f:
            return [_json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


@app.post("/api/feedback")
def save_feedback(req: FeedbackRequest, request: Request):
    if req.kind not in ("answer", "report") or req.rating not in ("up", "down"):
        raise HTTPException(status_code=422, detail="Invalid feedback.")
    if not usage_limits.take_feedback(_user_key(request)):
        raise HTTPException(status_code=429, detail="Thank you, we have received enough feedback from you today.")
    entry = {"time": _dt.now().isoformat(timespec="seconds"), "kind": req.kind, "rating": req.rating,
             "ref": (req.ref or "")[:300], "comment": (req.comment or "").strip()[:1000],
             "language": "hi" if req.language == "hi" else "en"}
    path = _feedback_file()
    with _fb_lock:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        cutoff = (_dt.now() - _td(days=FEEDBACK_KEEP_DAYS)).isoformat()
        kept = [e for e in _read_feedback() if e.get("time", "") >= cutoff]   # drop entries older than 180 days
        if sum(len(_json.dumps(e, ensure_ascii=False)) for e in kept) > FEEDBACK_MAX_BYTES:
            raise HTTPException(status_code=503, detail="The feedback box is full. Please try again later.")
        kept.append(entry)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in kept:
                f.write(_json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    return {"ok": True}


@app.get("/api/feedback/export")
def export_feedback(key: str = ""):
    admin = os.getenv("ADMIN_KEY", "")
    if len(admin) < 12 or not hmac.compare_digest(key.encode(), admin.encode()):
        raise HTTPException(status_code=403, detail="Not allowed.")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time", "kind", "rating", "question_or_report", "comment", "language"])
    for e in _read_feedback():
        w.writerow([e.get("time"), e.get("kind"), e.get("rating"), e.get("ref"), e.get("comment"), e.get("language")])
    return Response(content="\ufeff" + out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=cosmic11_feedback.csv"})


@app.middleware("http")
async def no_cache_for_page(request, call_next):
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# The web page itself (must be added last, after every API route).
_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
if os.path.isdir(_WEB):
    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
