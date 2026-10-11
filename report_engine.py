"""
report_engine.py - the paid guidance reports: 1-Year Monthly (12 months) and 5-Year Complete (12 months + years 2-5).

Built in the background, one year at a time. For each year: calculated facts (Swiss Ephemeris), the most
relevant book passages, a grounded AI write-up, the automatic accuracy check (one corrected retry if needed),
then Hindi translation if chosen. Progress is saved after every year so the page can show it live.
Reports are deleted automatically after KEEP_HOURS (we keep personal data only as long as needed).
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta

import requests

import book_licenses as bl
import forecast_engine as fe
import guardrails
import plain_layer as pl
import textscrub
import transit_meaning
from answer_checker import check_answer, correction_block, warning_text, house_lords_text

REPORT_DIR = os.getenv("REPORT_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports"))
KINDS = {"year1": {"months": 12, "years": 0}, "year5": {"months": 12, "years": 4}}   # year5 = 12 months + years 2-5
KEEP_HOURS = 48
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MONTH_QUESTION = "Give guidance for this month about career and work, relationships and marriage, and money."
YEAR_QUESTION = "Give guidance for this year about career and work, relationships and marriage, money, and family and home."


def call_gap():
    return int(os.getenv("REPORT_CALL_GAP", "62"))   # seconds between AI calls on Groq's free plan; set 0 on a paid plan


def _path(job_id):
    return os.path.join(REPORT_DIR, job_id + ".json")


def _save(job):
    os.makedirs(REPORT_DIR, exist_ok=True)
    tmp = _path(job["id"]) + ".tmp"
    json.dump(job, open(tmp, "w", encoding="utf-8"), default=str, ensure_ascii=False)
    os.replace(tmp, _path(job["id"]))


def load(job_id):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id or ""):
        return None
    try:
        return json.load(open(_path(job_id), encoding="utf-8"))
    except Exception:
        return None


def cleanup():
    if not os.path.isdir(REPORT_DIR):
        return
    cutoff = time.time() - KEEP_HOURS * 3600
    for f in os.listdir(REPORT_DIR):
        p = os.path.join(REPORT_DIR, f)
        if f.endswith(".json") and os.path.getmtime(p) < cutoff:
            os.remove(p)


def plan(kind, today):
    """The list of (kind, start, end) windows for a report."""
    k = KINDS[kind]
    out = [("month", fe._add_months(today, m), fe._add_months(today, m + 1) - timedelta(days=1)) for m in range(k["months"])]
    out += [("year", fe._add_years(today, y), fe._add_years(today, y + 1) - timedelta(days=1)) for y in range(1, 1 + k["years"])]
    return out


def start(birth: dict, lang: str, key: str, kind: str = "year1") -> str:
    total = KINDS[kind]["months"] + KINDS[kind]["years"]
    job = {"id": uuid.uuid4().hex, "status": "queued", "created": datetime.now().isoformat(timespec="seconds"),
           "lang": lang, "birth": birth, "kind": kind, "progress": 0, "total": total, "sections": []}
    _save(job)
    threading.Thread(target=_run, args=(job["id"], key), daemon=True).start()
    return job["id"]


def _llm(prompt, key):
    r = requests.post(GROQ_URL, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
                      json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 3000, "reasoning_format": "hidden", "reasoning_effort": "low"},
                      timeout=180).json()
    if "choices" not in r:
        raise RuntimeError(f"AI service error: {str(r)[:200]}")
    c = r["choices"][0]
    text = c["message"]["content"] or ""
    if c.get("finish_reason") == "length":
        text += "\n\n*(This section was cut short by the AI size limit.)*"
    return text


class _Pacer:
    """Keeps AI calls at least call_gap() seconds apart (Groq free plan: about one large request per minute)."""
    def __init__(self):
        self.last = 0.0

    def wait(self):
        gap = call_gap() - (time.time() - self.last)
        if self.last and gap > 0:
            time.sleep(gap)
        self.last = time.time()


def _run(job_id, key):
    import main as M                                     # imported here: main is fully loaded by now
    from chromadb.utils import embedding_functions
    import chromadb
    job = load(job_id)
    try:
        b = job["birth"]
        birth_dt = datetime.combine(date.fromisoformat(b["birth_date"]), datetime.strptime(b["birth_time"][:5], "%H:%M").time())
        tz = float(b.get("utc_offset", 5.5))
        chart = M.calculate_chart(birth_dt, float(b["latitude"]), float(b["longitude"]), tz)
        col = chromadb.PersistentClient(path=M.CHROMA_PATH).get_collection(
            M.COLLECTION_NAME, embedding_function=embedding_functions.DefaultEmbeddingFunction())
        job["chart"] = {"asc_sign": chart.ascendant_sign, "asc_deg": round(chart.ascendant_deg, 2), "asc_nak": chart.ascendant_nakshatra,
                        "placements": [{"planet": p.planet, "sign": p.sign, "deg": round(p.sign_lord_deg, 2), "house": p.house,
                                        "nakshatra": p.nakshatra, "pada": p.pada, "retro": bool(p.retrograde)} for p in chart.placements]}
        today = date.today()
        windows = plan(job["kind"], today)
        overview = fe.build_forecast(chart, birth_dt, tz, today, windows[-1][2], fast=False)
        job["overview"] = {"mahadashas": overview["mahadashas"], "sade_sati": overview["sade_sati"],
                           "moon_sign": overview["moon_sign"], "lagna": overview["lagna"]}
        job["key_dates"] = pl.key_dates(overview)
        job["timeline"] = pl.timeline_data(overview)
        meaning_cache = {}                                          # (planet, house) -> passage, shared by all sections of this report
        job["status"] = "running"
        _save(job)

        seeker = b.get("name") or "the seeker"
        chart_summary = "\n".join(f"- {p.planet} in {p.sign}, house {p.house}, {p.nakshatra} pada {p.pada}"
                                  f"{' (retrograde)' if p.retrograde else ''}" for p in chart.placements)
        chart_summary += "\n" + house_lords_text(chart.ascendant_sign)
        pacer = _Pacer()
        for k, (wkind, ys, ye) in enumerate(windows):
            question = MONTH_QUESTION if wkind == "month" else YEAR_QUESTION
            facts = fe.build_forecast(chart, birth_dt, tz, ys, ye, fast=(wkind == "month"))
            ftxt = fe.forecast_text(facts, f"{wkind} {k + 1} of {len(windows)}")             # technical timeline: shown in the page, never written by the AI
            meanings = transit_meaning.fetch(col, facts, {"domain": "astrology"}, cache=meaning_cache)
            plain = pl.plain_facts_text(facts, chart, f"{wkind} {k + 1} of {len(windows)}", short=(wkind == "month"), meanings=meanings)
            queries = fe.merge_queries(M.build_retrieval_queries(chart, "", question), fe.retrieval_queries(facts, chart, 16))
            per_query = []
            for q in queries:
                r = col.query(query_texts=[q], n_results=3, where={"domain": "astrology"})
                per_query.append([(d, m["source"]) for d, m in zip(r["documents"][0], r["metadatas"][0])])
            picked = fe.round_robin(per_query, 24)
            docs, srcs = bl.drop_excluded([d for d, _ in picked], [s for _, s in picked])
            docs, srcs = textscrub.filter_passages(docs, srcs)          # no OCR junk reaches the AI
            prompt, docs, srcs = fe.fit_prompt(
                lambda d_, s_: bl.with_quote_rules(pl.report_prompt(wkind, seeker, plain, bl.tag_passages(d_, s_))),
                docs, srcs, 3000)
            pacer.wait()
            text = pl.tidy_text(pl.clean_output(_llm(prompt, key)))
            acc, style = check_answer(text, chart, facts), pl.lint_plain(text, wkind)
            if acc or style:                                        # one rewrite that fixes facts AND wording
                pacer.wait()
                try:
                    text2 = pl.tidy_text(pl.clean_output(_llm(prompt + correction_block(acc + style), key)))
                    acc2, style2 = check_answer(text2, chart, facts), pl.lint_plain(text2, wkind)
                    if 3 * len(acc2) + len(style2) <= 3 * len(acc) + len(style):
                        text, acc, style = text2, acc2, style2
                except RuntimeError:
                    pass
            issues = acc                                            # only accuracy problems are shown to the reader
            if issues:
                text += warning_text(issues)
            out_lang = "en"
            if job["lang"] == "hi":
                hi = M.translate_to_hindi(text, key)
                if hi:
                    text, out_lang = hi, "hi"
            job["sections"].append({"index": k + 1, "kind": wkind, "start": ys, "end": ye, "facts_text": ftxt, "text": text,
                                    "language": out_lang, "sources": pl.cited_sources(text, srcs), "issues": len(issues),
                                    "style_issues": len(style),
                                    "running": [{"start": x["start"], "end": x["end"], "main": x["main"], "sub": x["sub"], "short": x["short"]}
                                                for x in pl.running_segments(facts, short=(wkind == "month"))]})
            job["progress"] = k + 1
            _save(job)
        job["status"] = "done"
        job["disclaimer"] = guardrails.disclaimer(job["lang"])
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)[:300]
    _save(job)
