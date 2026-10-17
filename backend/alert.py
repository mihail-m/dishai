"""One PM2.5 number for every screen: the city-level level from the learned model, and the alert built from it.

Before this, the banner came from the stagnation score, the chart from raw CAMS and the street map from the learned model, and
the three disagreed by a factor of two to three on episode hours (see docs/PM_MODEL.md). Here the hourly city level comes from
`PmModel.level_pm` (the same call the street map uses), the alert fires on that level against health thresholds, and the
stagnation score stays as supporting text ("the air is trapped") and a chart overlay.

Fallbacks keep the app running without the model file: CAMS x the live-sensor bias (the pre-ML background), and as a last
resort the old stagnation-score rule. `basis` says which one produced the numbers.
"""
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .cai import config as C
from .ml.pm_model import MAX_LEAD_H, MODEL_PATH, PmModel, anchor, hour_vector, now_vector
from .score import label_for

LEVEL_LABEL = {"good": "Fine", "watch": "Sensitive groups", "warning": "Everyone"}
_model_cache: dict = {"mtime": None, "model": None}


def get_model(path: Path = MODEL_PATH) -> PmModel | None:
    """The trained model, reloaded only when the file changes (joblib decompression is slow enough to matter per request)."""
    if not C.USE_ML:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _model_cache["mtime"] != mtime:
        _model_cache.update(mtime=mtime, model=PmModel.load(path))
    return _model_cache["model"]


def level_for_pm(pm: float) -> str:
    return "warning" if pm >= C.PM_WARNING else "watch" if pm >= C.PM_WATCH else "good"


def _typed(h: dict) -> tuple[int, bool]:
    t = datetime.strptime(h["time"], "%Y-%m-%dT%H:%M")
    return t.hour, t.weekday() >= 5


def city_levels(hours: list[dict], now_idx: int, sensor_pm25: list[float], model: PmModel | None) -> tuple[list[float | None], str]:
    """(PM2.5 per forecast hour in the model's units, basis). Entries are None when nothing can be said for the hour."""
    n = len(hours)
    pm = np.asarray(sensor_pm25, dtype=float)
    med = float(np.median(np.clip(pm, 0, 4 * np.median(pm) + 10))) if len(pm) else float("nan")
    cams_now = hours[now_idx].get("pm25") if n else None
    cams_now = float("nan") if cams_now is None else float(cams_now)
    if model is not None and n:
        hv = [hour_vector(h, hour=_typed(h)[0], weekend=_typed(h)[1]) for h in hours]
        nv = [now_vector(min(max(0, i - now_idx), MAX_LEAD_H), med, cams_now) for i in range(n)]
        x = np.asarray([a + b for a, b in zip(hv, nv)], dtype="float32")
        base = np.array([anchor(a[0], b[1]) for a, b in zip(hv, nv)])
        out = np.expm1(np.log1p(base) + model.level.predict(x)).clip(0)
        return [round(float(v), 1) for v in out], "model"
    if np.isfinite(cams_now) and np.isfinite(med):
        bias = float(np.clip(med / max(cams_now, C.CAMS_FLOOR), C.BIAS_MIN, C.BIAS_MAX))
        return [None if h.get("pm25") is None else round(float(h["pm25"]) * bias, 1) for h in hours], "cams_bias"
    return [None] * n, "stagnation"


def _hhmm(t: str) -> str:
    return t[11:16]


def _day_word(start: datetime, now: datetime) -> str:
    night = start.hour >= 17 or start.hour < 5
    if start.date() == now.date():
        return "tonight" if night else "today"
    return "tomorrow night" if night else "tomorrow"


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    out, i, n = [], 0, len(flags)
    while i < n:
        if flags[i]:
            j = i
            while j + 1 < n and flags[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def build_alert(times: list[str], pm: list[float | None], scores: list[float], now_idx: int, now: datetime,
                basis: str = "model", calibrated: bool = False) -> dict:
    """The banner. Active when the level stays at or above the EU limit for ALERT_MIN_HOURS in a row, starting within 24 h of now
    (or already running). `peak_score` is the stagnation score at its 24 h maximum: context, not the trigger."""
    n = len(times)
    horizon = range(now_idx, min(now_idx + 24, n))
    sc = scores[now_idx : now_idx + 24]
    peak_s = now_idx + max(range(len(sc)), key=lambda k: sc[k]) if sc else now_idx
    base = {
        "basis": basis, "scale": "reference" if calibrated else "sensor",
        "peak_score": round(scores[peak_s], 1) if n else 0, "peak_score_time": times[peak_s] if n else None,
        "trapped": bool(n) and label_for(scores[peak_s]) == "warning",
    }
    if basis == "stagnation":  # no PM series at all: the old rule, flagged
        return _stagnation_alert(times, scores, now_idx, now, base)

    flags = [p is not None and p >= C.PM_WARNING for p in pm]
    run = next(((a, b) for a, b in _runs(flags) if b >= now_idx and a < now_idx + 24 and b - a + 1 >= C.ALERT_MIN_HOURS), None)
    vals = [pm[i] for i in horizon if pm[i] is not None]
    peak_i = max((i for i in horizon if pm[i] is not None), key=lambda i: pm[i], default=None)
    base.update(peak_pm25=round(pm[peak_i], 1) if peak_i is not None else None, peak_time=times[peak_i] if peak_i is not None else None)
    if run is None:
        top = max(vals) if vals else None
        level = "watch" if top is not None and top >= C.PM_WATCH else "good"
        base.update(active=False, level=level, label=LEVEL_LABEL[level], audience="sensitive" if level == "watch" else "none")
        if level == "watch":
            msg = f"PM2.5 up to {top:.0f} µg/m³ around {_hhmm(base['peak_time'])}. Sensitive groups may want to take it easy."
        else:
            msg = "No pollution episode expected in the next 24 hours."
        return {**base, "message": msg}

    a, b = run
    seg = [pm[i] for i in range(a, b + 1)]
    mean, peak = float(np.mean(seg)), float(max(seg))
    audience = "everyone" if peak >= C.PM_EVERYONE else "sensitive"
    end_time = (datetime.strptime(times[b], "%Y-%m-%dT%H:%M") + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    in_progress = a <= now_idx
    when = "Now until " + _hhmm(end_time) if in_progress else f"{_day_word(datetime.strptime(times[a], '%Y-%m-%dT%H:%M'), now).capitalize()} {_hhmm(times[a])}–{_hhmm(end_time)}"
    advice = "Everyone should limit time outside." if audience == "everyone" else "Sensitive groups should limit time outside."
    return {
        **base, "active": True, "in_progress": in_progress, "start": times[a], "end": end_time, "start_hhmm": _hhmm(times[a]),
        "end_hhmm": _hhmm(end_time), "hours": b - a + 1, "level": "warning", "label": LEVEL_LABEL["warning"], "audience": audience,
        "mean_pm25": round(mean, 1), "peak_pm25": round(peak, 1),
        "message": f"{when}: PM2.5 around {mean:.0f} µg/m³. {advice}",
    }


def _stagnation_alert(times: list[str], scores: list[float], now_idx: int, now: datetime, base: dict) -> dict:
    """Pre-model rule, used only when neither the model nor CAMS plus sensors give a PM2.5 series."""
    n = len(scores)
    start = next((i for i in range(now_idx, min(now_idx + 24, n)) if label_for(scores[i]) == "warning"), None)
    base = {**base, "peak_time": base["peak_score_time"], "peak_pm25": None}
    if start is None:
        return {**base, "active": False, "level": "good", "label": LEVEL_LABEL["good"], "audience": "none",
                "message": "No inversion warning in the next 24 hours."}
    end = start
    while end + 1 < n and label_for(scores[end + 1]) == "warning":
        end += 1
    end_time = (datetime.strptime(times[end], "%Y-%m-%dT%H:%M") + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    in_progress = start == now_idx
    when = "Inversion in progress until " + _hhmm(end_time) if in_progress else \
        f"Inversion expected {_day_word(datetime.strptime(times[start], '%Y-%m-%dT%H:%M'), now)} {_hhmm(times[start])} to {_hhmm(end_time)}"
    return {**base, "active": True, "in_progress": in_progress, "start": times[start], "end": end_time, "start_hhmm": _hhmm(times[start]),
            "end_hhmm": _hhmm(end_time), "hours": end - start + 1, "level": "warning", "label": LEVEL_LABEL["warning"],
            "audience": "sensitive", "message": when}


def best_window(times: list[str], series: list[float | None], now_idx: int) -> dict | None:
    """Best daytime (10:00-18:00) two-hour slot in the next 24 h: lowest series value (PM2.5, or the stagnation score as fallback)."""
    cands = [i for i in range(now_idx, min(now_idx + 24, len(series) - 1))
             if 10 <= int(times[i][11:13]) <= 17 and series[i] is not None and series[i + 1] is not None]
    if not cands:
        return None
    i = min(cands, key=lambda k: series[k] + series[k + 1])
    end = (datetime.strptime(times[i], "%Y-%m-%dT%H:%M") + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    return {"start": times[i], "end": end, "start_hhmm": _hhmm(times[i]), "end_hhmm": _hhmm(end)}
