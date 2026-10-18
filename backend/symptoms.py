"""Anonymous daily symptom check-ins, to compare how people feel with what the pollen model says.

Stored per report: local date, district, severity and the model's pollen level that day. No identifier, no IP, no free text, so one person
sending twice cannot be told apart (the app asks once a day, on the device). Reports sit in data/symptoms.jsonl (git-ignored).
The summary only claims a pattern once a pollen level has MIN_N reports; until then it says how many more are needed.
"""
import json
import threading
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

STORE = Path(__file__).resolve().parent.parent / "data" / "symptoms.jsonl"
Severity = Literal["none", "mild", "bad"]
LEVELS = ("none", "low", "moderate", "high")
MIN_N = 20
MAX_PER_DAY = 500  # bound abuse of an unauthenticated endpoint
_lock = threading.Lock()


def _read(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue  # a torn last line must not break the summary
    return out


def record(day: date, district: str, severity: str, level: str, top: str | None, path: Path = STORE) -> bool:
    """Append one report. False when today's cap is reached."""
    with _lock:
        today = [r for r in _read(path) if r.get("date") == day.isoformat()]
        if len(today) >= MAX_PER_DAY:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"date": day.isoformat(), "district": district, "severity": severity, "level": level, "top": top}, ensure_ascii=False) + "\n")
    return True


def summary(today: date, days: int = 30, path: Path = STORE) -> dict:
    since = (today - timedelta(days=days)).isoformat()
    rows = [r for r in _read(path) if r.get("date", "") >= since and r.get("level") in LEVELS and r.get("severity") in ("none", "mild", "bad")]
    by_level: dict[str, Counter] = defaultdict(Counter)
    by_day: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_level[r["level"]][r["severity"]] += 1
        by_day[r["date"]][r["severity"]] += 1
    levels = []
    for lv in LEVELS:
        c = by_level.get(lv, Counter())
        n = sum(c.values())
        levels.append({"level": lv, "n": n, "bad": c["bad"], "mild": c["mild"], "none": c["none"],
                       "bad_share": round(c["bad"] / n, 2) if n >= MIN_N else None, "enough": n >= MIN_N})
    return {
        "reports": len(rows), "days": days, "min_n": MIN_N, "levels": levels,
        "daily": [{"date": d, **{k: c[k] for k in ("none", "mild", "bad")}} for d, c in sorted(by_day.items())],
        "note": "Anonymous self-reports; one person can report on several days and nothing stops a repeat on the same day. "
                f"A level needs {MIN_N} reports before a share is shown. This shows whether the model's pollen level tracks how people feel in Sofia; it is not a medical statistic.",
    }
