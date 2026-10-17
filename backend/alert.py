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

Z80 = 1.2815515655446004  # standard normal quantile of the p10 / p90
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


def exceed_probability(u_lo, u50, u_hi, thr: float = C.PM_WARNING, min_hours: int = C.ALERT_MIN_HOURS, phi: float = C.ALERT_PHI,
                       n: int = 1000, seed: int = 11) -> np.ndarray:
    """P(level >= thr for at least `min_hours` consecutive hours somewhere in the window).

    u_* are log1p(PM2.5) at p10 / p50 / p90 per hour, shape (H,) or (B, H) for B windows. Each hour is a two-piece normal through
    those quantiles, and the standardised errors of consecutive hours follow an AR(1) process with lag-1 correlation `phi` (measured
    in the back-test: a forecast that is too low at 22:00 is nearly always too low at 23:00, much less so 12 hours later). The random
    numbers are the same for every window, so the probability is monotone in the level: a higher level can never lower it."""
    u50 = np.asarray(u50, dtype=float)
    one = u50.ndim == 1
    u_lo, u50, u_hi = (np.atleast_2d(np.asarray(a, dtype=float)) for a in (u_lo, u50, u_hi))
    B, H = u50.shape
    if min_hours > H:
        return np.zeros(B)
    rng = np.random.default_rng(seed)
    eta = rng.standard_normal((n, H))
    z = np.empty((n, H))
    z[:, 0] = eta[:, 0]
    for h in range(1, H):
        z[:, h] = phi * z[:, h - 1] + np.sqrt(1 - phi**2) * eta[:, h]
    sd_lo, sd_hi = np.maximum(u50 - u_lo, 0) / Z80, np.maximum(u_hi - u50, 0) / Z80
    u = u50[None] + np.where(z[:, None, :] >= 0, sd_hi[None], sd_lo[None]) * z[:, None, :]  # (n, B, H)
    over = u >= np.log1p(thr)
    c = np.concatenate([np.zeros((n, B, 1)), np.cumsum(over, axis=2)], axis=2)
    run = ((c[:, :, min_hours:] - c[:, :, :-min_hours]) >= min_hours).any(axis=2)
    p = run.mean(axis=0)
    return p[0] if one else p


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


def alert_settings(model: PmModel | None) -> dict:
    """Thresholds and error correlation for the probabilistic alert: the values the back-test stored in the model file, else config."""
    a = (model.meta.get("alert") if model is not None else None) or {}
    return {"p_red": a.get("p_red", C.ALERT_P), "p_watch": a.get("p_watch", C.ALERT_P_WATCH), "phi": a.get("phi", C.ALERT_PHI)}


DISTRICT_SHRINK_N = 5  # a district with n sensors counts n/(n+5) of its own deviation from the city, the rest is the city value


def district_log_ratios(sensors: list[dict]) -> dict[str, tuple[float, int]]:
    """district -> (log1p ratio of the district's typical sensor to the city's, number of sensors). Uses the offset-adjusted readings
    (a persistently over-reading unit must not make its district look dirty) and shrinks toward the city value for few sensors."""
    by: dict[str, list[float]] = {}
    for s in sensors:
        if s.get("district"):
            by.setdefault(s["district"], []).append(float(s.get("pm25_adjusted", s["pm25"])))
    if not by:
        return {}
    city = float(np.median([v for vs in by.values() for v in vs]))
    out = {}
    for d, vs in by.items():
        n = len(vs)
        out[d] = (n / (n + DISTRICT_SHRINK_N) * (np.log1p(float(np.median(vs))) - np.log1p(city)), n)
    return out


def district_forecast(fc: dict, log_ratio: float, rho_fn=None) -> tuple[list, tuple | None]:
    """The city's hourly p50 and p10..p90 band moved to a district: shift in log1p space by the district's deviation, which fades with the
    lead like the street-level correction does (rho_fn(lead) from the spatial file). Returns (p50 series, band or None)."""
    now = fc["now_index"]
    hs = fc["hours"]
    shift = np.array([log_ratio * (rho_fn(max(0, i - now)) if rho_fn else 1.0) for i in range(len(hs))])
    mv = lambda key: np.array([np.nan if h.get(key) is None else h[key] for h in hs], dtype=float)
    mv_u = lambda key: np.expm1(np.log1p(mv(key)) + shift).clip(0)
    mid = mv_u("pm25_model")
    series = [None if np.isnan(v) else round(float(v), 1) for v in mid]
    if np.isnan(mv("pm25_p10")).any() or np.isnan(mv("pm25_p90")).any():
        return series, None
    return series, (mv_u("pm25_p10"), mid, mv_u("pm25_p90"))


def level_band(hours: list[dict], now_idx: int, sensor_pm25: list[float], model: PmModel | None):
    """(p10, p50, p90) of the city PM2.5 per forecast hour as arrays in ug/m3, or None when the model has no interval models."""
    if model is None or model.q_lo is None or not hours:
        return None
    pm = np.asarray(sensor_pm25, dtype=float)
    med = float(np.median(np.clip(pm, 0, 4 * np.median(pm) + 10))) if len(pm) else float("nan")
    cams_now = hours[now_idx].get("pm25")
    cams_now = float("nan") if cams_now is None else float(cams_now)
    hv = [hour_vector(h, hour=_typed(h)[0], weekend=_typed(h)[1]) for h in hours]
    nv = [now_vector(min(max(0, i - now_idx), MAX_LEAD_H), med, cams_now) for i in range(len(hours))]
    lo, mid, hi = model.level_quantiles(hv, nv)
    return np.expm1(lo).clip(0), np.expm1(mid).clip(0), np.expm1(hi).clip(0)


def build_alert(times: list[str], pm: list[float | None], scores: list[float], now_idx: int, now: datetime,
                basis: str = "model", calibrated: bool = False, band=None, settings: dict | None = None) -> dict:
    """The banner.

    With a p10/p90 band (`band`, from level_band) it is probabilistic: P = chance that the city level stays at or above the EU limit
    for ALERT_MIN_HOURS in a row somewhere in the next 24 h. Red ("warning") from `p_red`, amber ("watch") from `p_watch`.
    Without a band it is the deterministic rule on the median level. `peak_score` is the stagnation score at its 24 h maximum:
    context, not the trigger."""
    n = len(times)
    horizon = range(now_idx, min(now_idx + 24, n))
    sc = scores[now_idx : now_idx + 24]
    peak_s = now_idx + max(range(len(sc)), key=lambda k: sc[k]) if sc else now_idx
    base = {
        "basis": basis, "scale": "reference" if calibrated else "sensor",
        "peak_score": round(scores[peak_s], 1) if n else 0, "peak_score_time": times[peak_s] if n else None,
        "trapped": bool(n) and label_for(scores[peak_s]) == "warning", "probability": None,
    }
    if basis == "stagnation":  # no PM series at all: the old rule, flagged
        return _stagnation_alert(times, scores, now_idx, now, base)

    st = settings or alert_settings(None)
    in_window = lambda a, b: b >= now_idx and a < now_idx + 24 and b - a + 1 >= C.ALERT_MIN_HOURS
    runs_of = lambda series: next(((a, b) for a, b in _runs([v is not None and v >= C.PM_WARNING for v in series]) if in_window(a, b)), None)
    run = runs_of(pm)
    vals = [pm[i] for i in horizon if pm[i] is not None]
    peak_i = max((i for i in horizon if pm[i] is not None), key=lambda i: pm[i], default=None)
    base.update(peak_pm25=round(pm[peak_i], 1) if peak_i is not None else None, peak_time=times[peak_i] if peak_i is not None else None)

    if band is not None:
        lo, mid, hi = (np.asarray(b, dtype=float) for b in band)
        sl = slice(now_idx, min(now_idx + 24, n))
        prob = float(exceed_probability(np.log1p(lo[sl]), np.log1p(mid[sl]), np.log1p(hi[sl]), phi=st["phi"]))
        base.update(probability=round(prob, 2), p_red=st["p_red"], p_watch=st["p_watch"])
        base["peak_p90"] = round(float(hi[sl].max()), 1) if len(hi[sl]) else None
        if run is None:  # the median never makes it, but the upper band can: show when it would
            run = runs_of(list(hi))
        level = "warning" if prob >= st["p_red"] else "watch" if prob >= st["p_watch"] else None
    else:
        prob, level = None, ("warning" if run is not None else None)
    if level is None:
        top = max(vals) if vals else None
        level = "watch" if top is not None and top >= C.PM_WATCH else "good"
        base.update(active=False, level=level, label=LEVEL_LABEL[level], audience="sensitive" if level == "watch" else "none")
        msg = (f"PM2.5 up to {top:.0f} µg/m³ around {_hhmm(base['peak_time'])}. Sensitive groups may want to take it easy."
               if level == "watch" else "No pollution episode expected in the next 24 hours.")
        return {**base, "message": msg}

    if run is None:  # probability high but no run to describe (very short window at the end of the series)
        run = (max(now_idx, 0), min(now_idx + 2, n - 1))
    a, b = run
    seg = [v for v in (pm[i] for i in range(a, b + 1)) if v is not None]
    mean, peak = float(np.mean(seg)), float(max(seg))
    if band is not None:
        peak = max(peak, float(np.max(mid[a : b + 1])))
    audience = "everyone" if peak >= C.PM_EVERYONE else "sensitive"
    end_time = (datetime.strptime(times[b], "%Y-%m-%dT%H:%M") + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    in_progress = a <= now_idx
    when = "Now until " + _hhmm(end_time) if in_progress else f"{_day_word(datetime.strptime(times[a], '%Y-%m-%dT%H:%M'), now).capitalize()} {_hhmm(times[a])}–{_hhmm(end_time)}"
    chance = f" ({prob:.0%} chance)" if prob is not None else ""
    if level == "warning":
        advice = "Everyone should limit time outside." if audience == "everyone" else "Sensitive groups should limit time outside."
        message = f"{when}: PM2.5 around {mean:.0f} µg/m³{chance}. {advice}"
    else:
        message = f"Possible episode {when[0].lower() + when[1:]}: PM2.5 may reach {max(peak, mean):.0f} µg/m³{chance}. Sensitive groups should keep an eye on it."
    return {
        **base, "active": level == "warning", "in_progress": in_progress, "start": times[a], "end": end_time, "start_hhmm": _hhmm(times[a]),
        "end_hhmm": _hhmm(end_time), "hours": b - a + 1, "level": level, "label": LEVEL_LABEL[level], "audience": audience,
        "mean_pm25": round(mean, 1), "peak_pm25": round(peak, 1) if band is not None and peak > (base["peak_pm25"] or 0) else base["peak_pm25"],
        "message": message,
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
