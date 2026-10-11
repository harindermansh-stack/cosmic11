"""usage_limits.py - protects the Groq budget: a daily limit per person and a daily limit for everyone together.
Counts are kept in usage.json and reset automatically each day (Indian time)."""
import json
import os
import threading
from datetime import datetime, timedelta, timezone

from rag_config import DAILY_PER_USER, DAILY_TOTAL

_LOCK = threading.Lock()
FILE = os.getenv("USAGE_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage.json"))


def _today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()


def _load():
    try:
        data = json.load(open(FILE, encoding="utf-8"))
    except Exception:
        data = {}
    if data.get("date") != _today():
        data = {"date": _today(), "total": 0, "users": {}}
    return data


def take(user_key):
    """Reserve one question. Returns (allowed, remaining_for_user, reason)."""
    with _LOCK:
        d = _load()
        used = d["users"].get(user_key, 0)
        if d["total"] >= DAILY_TOTAL:
            return False, 0, "total"
        if used >= DAILY_PER_USER:
            return False, 0, "user"
        d["users"][user_key] = used + 1
        d["total"] += 1
        json.dump(d, open(FILE, "w", encoding="utf-8"))
        return True, DAILY_PER_USER - used - 1, ""


def give_back(user_key):
    """Undo a reservation when the reading failed, so the person is not charged a question."""
    with _LOCK:
        d = _load()
        if d["users"].get(user_key, 0) > 0:
            d["users"][user_key] -= 1
            d["total"] = max(0, d["total"] - 1)
            json.dump(d, open(FILE, "w", encoding="utf-8"))


def take_report(user_key):
    """Reserve one report for today (separate limits from questions). Returns (allowed, reason)."""
    from rag_config import REPORTS_PER_USER, REPORTS_TOTAL
    with _LOCK:
        d = _load()
        users = d.setdefault("report_users", {})
        total = d.get("report_total", 0)
        if total >= REPORTS_TOTAL:
            return False, "total"
        if users.get(user_key, 0) >= REPORTS_PER_USER:
            return False, "user"
        users[user_key] = users.get(user_key, 0) + 1
        d["report_total"] = total + 1
        json.dump(d, open(FILE, "w", encoding="utf-8"))
        return True, ""


def take_feedback(user_key, limit=20):
    """At most `limit` feedback messages per person per day (stops spam)."""
    with _LOCK:
        d = _load()
        fb = d.setdefault("feedback_users", {})
        if fb.get(user_key, 0) >= limit:
            return False
        fb[user_key] = fb.get(user_key, 0) + 1
        json.dump(d, open(FILE, "w", encoding="utf-8"))
        return True
