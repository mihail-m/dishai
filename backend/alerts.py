"""One list of things the user should know about in the next 48 hours: pollution episodes, heat, mineral dust and smoke from fires.

Every kind answers the same questions (what, when, how bad, how sure, what to do), so the banner, the suggestions and the push
notifications all read the same objects instead of each re-deriving them. The alert id is stable (kind + start day), the level can escalate;
a notification is sent once per (id, level).

Sources, and what each can honestly claim:
  pm    the city PM2.5 episode forecast (backend/alert.py): the learned model, back-tested in docs/PM_MODEL.md. CAMS, which feeds that model,
        already carries fire and dust emissions, so a smoke or dust episode raises this alert too; the other kinds below say WHY.
  heat  the city feels-like forecast against the advice bands (backend/advice.py HEAT_BANDS).
  dust  CAMS mineral dust (Copernicus Atmosphere Monitoring Service, via Open-Meteo, no key). Thresholds come from the history of the field itself
        (Jan 2025 - Oct 2026: dust reached 20 ug/m3 in 12 days and 40 in 3 days); a check against Sofia station PM10 had too few dust days in 2026 to
        validate them (docs/ALERTS.md).
  fire  active-fire detections (NASA FIRMS, VIIRS, via a free MAP_KEY) upwind of Sofia at 850 hPa. Indicative only: it says smoke may arrive,
        not how much. Without a key this kind is simply absent and the response says so. (The EFFIS / GWIS web services were returning server
        errors when checked, so they are not used.)
Tested and dropped: a "smoke" rule from CAMS aerosol optical depth and carbon monoxide. It fired 69 times in 21 months, almost all ordinary haze.
"""
from datetime import datetime, timedelta

from .advice import HEAT_BANDS

LEVELS = ("info", "watch", "warning")
KINDS = ("pm", "heat", "dust", "fire")
DUST_WATCH, DUST_WARNING = 20.0, 40.0  # ug/m3, CAMS dust
DUST_MIN_HOURS = 2
HEAT_MIN_HOURS = 2
HORIZON_H = 48


def rank(level: str) -> int:
    return LEVELS.index(level)


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


def _end_time(t: str) -> str:
    return (datetime.strptime(t, "%Y-%m-%dT%H:%M") + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")


def _when(start: str, end: str, now: str) -> str:
    s, e = datetime.strptime(start, "%Y-%m-%dT%H:%M"), datetime.strptime(end, "%Y-%m-%dT%H:%M")
    n = datetime.strptime(now, "%Y-%m-%dT%H:%M")
    if s <= n:
        return f"until {end[11:16]}" + ("" if e.date() == n.date() else f" {e.strftime('%a')}")
    day = "today" if s.date() == n.date() else "tomorrow" if s.date() == n.date() + timedelta(days=1) else s.strftime("%A")
    return f"{day} {start[11:16]}–{end[11:16]}"


ACTIONS = {
    "pm": {
        "warning": [("windows", "Keep windows closed", "Air outside is above the EU daily limit for the hours below."),
                    ("purifier", "Run an air purifier", "In the room you use most, if you have one."),
                    ("exercise", "Skip outdoor exercise", "If you must go out, use a park or a quiet street and consider an FFP2 mask.")],
        "watch": [("airing", "Air your home before it starts", "Ventilate briefly while the air is still clean."),
                  ("sensitive", "Sensitive groups: keep trips short", "People with asthma, older people and small children.")],
    },
    "heat": {
        "warning": [("shade", "Stay in the coolest place you can", "Avoid exertion at midday and in the afternoon."),
                    ("water", "Drink water before you are thirsty", "Check on older neighbours and relatives."),
                    ("night", "Cool the home after dark", "Open windows when it is cooler outside than inside, and keep blinds closed by day.")],
        "watch": [("shade", "Plan outdoor tasks for the morning or evening", "Keep to the shade in the hot hours."),
                  ("water", "Drink water and wear light clothes", "")],
        "info": [("water", "Warm day: drink water", "Heat-sensitive people should limit exertion in the afternoon.")],
    },
    "dust": {
        "warning": [("windows", "Keep windows closed in the dust hours", "Desert dust adds coarse particles that can aggravate asthma and hay fever."),
                    ("clean", "Wet-clean surfaces rather than dry-sweeping", "")],
        "watch": [("windows", "Close windows facing the wind during the dust hours", "Mineral dust is carried over the region."),
                  ("sensitive", "Asthma or hay fever: keep reliever medication to hand", "")],
    },
    "fire": {
        "watch": [("windows", "Be ready to close windows if you smell smoke", "Smoke from fires to the upwind side may reach the city."),
                  ("sensitive", "Sensitive groups: watch the PM2.5 forecast", "Smoke raises fine particles; the forecast is updated hourly."),
                  ("burn", "Do not add smoke yourself", "Avoid burning wood, leaves or waste while smoke is expected.")],
    },
}


def _alert(kind: str, level: str, start: str, end: str, now: str, title: str, message: str, *, peak: dict | None = None, cause: list[str] | None = None,
           source: str, confidence: str, audience: str = "everyone") -> dict:
    acts = ACTIONS.get(kind, {}).get(level) or ACTIONS.get(kind, {}).get("watch", [])
    return {
        "id": f"{kind}-{start[:10]}", "kind": kind, "level": level, "title": title, "message": message, "start": start, "end": end,
        "in_progress": start <= now < end, "peak": peak, "cause": cause or [], "source": source, "confidence": confidence, "audience": audience,
        "actions": [{"id": i, "title": t, "body": b} for i, t, b in acts],
    }


# ---- pm -------------------------------------------------------------------------------------------------------------------------------------
def pm_alert(fc: dict, now: str) -> dict | None:
    a = fc.get("alert") or {}
    if a.get("level") not in ("watch", "warning") or not a.get("start"):
        return None
    prob = a.get("probability")
    chance = f" ({prob:.0%} chance)" if prob is not None else ""
    pk = a.get("peak_pm25")
    mean = a.get("mean_pm25") if a.get("mean_pm25") is not None else pk
    title = "Poor air for everyone" if a["level"] == "warning" and a.get("audience") == "everyone" else "Poor air expected" if a["level"] == "warning" else "Possible pollution episode"
    msg = f"PM2.5 around {mean:.0f} µg/m³ {_when(a['start'], a['end'], now)}{chance}." if mean is not None else a.get("message", "")
    return _alert("pm", a["level"], a["start"], a["end"], now, title, msg, peak={"value": pk, "unit": "µg/m³", "time": a.get("peak_time")} if pk is not None else None,
                  source="DishAI PM2.5 model (CAMS + citizen sensors)", confidence="model", audience=a.get("audience") or "everyone")


# ---- heat -----------------------------------------------------------------------------------------------------------------------------------
def heat_alerts(hours: list[dict], now_idx: int, now: str) -> list[dict]:
    """One alert per stretch of feels-like >= 27 C (the first heat band). Its level is set by the peak: 27 info, 32 watch, 38 warning."""
    sl = hours[now_idx : now_idx + HORIZON_H]
    app = [h.get("apparent") if h.get("apparent") is not None else h["t2m"] for h in sl]
    out = []
    for a, b in _runs([v >= HEAT_BANDS[0] for v in app]):
        if b - a + 1 < HEAT_MIN_HOURS:
            continue
        peak_i = max(range(a, b + 1), key=lambda k: app[k])
        peak = app[peak_i]
        lvl = "warning" if peak >= HEAT_BANDS[2] else "watch" if peak >= HEAT_BANDS[1] else "info"
        start, end = sl[a]["time"], _end_time(sl[b]["time"])
        word = {"warning": "Extreme heat", "watch": "Hot", "info": "Warm"}[lvl]
        out.append(_alert("heat", lvl, start, end, now, f"{word}: feels like {peak:.0f} °C", f"Peak {peak:.0f} °C around {sl[peak_i]['time'][11:16]}, {_when(start, end, now)}.",
                          peak={"value": round(peak, 1), "unit": "°C", "time": sl[peak_i]["time"]}, source="Open-Meteo forecast, feels-like temperature",
                          confidence="forecast", audience="sensitive" if lvl == "info" else "everyone"))
    return out


# ---- dust -----------------------------------------------------------------------------------------------------------------------------------
def dust_alerts(series: list[dict], now: str) -> list[dict]:
    """series: [{time, dust}] from CAMS. One alert per run of at least DUST_MIN_HOURS hours at or above the watch threshold."""
    rows = [r for r in series if r["time"] >= now[:13] + ":00"][:HORIZON_H]
    vals = [r.get("dust") if r.get("dust") is not None else 0.0 for r in rows]
    out = []
    for a, b in _runs([v >= DUST_WATCH for v in vals]):
        if b - a + 1 < DUST_MIN_HOURS:
            continue
        peak_i = max(range(a, b + 1), key=lambda k: vals[k])
        peak = vals[peak_i]
        lvl = "warning" if peak >= DUST_WARNING else "watch"
        start, end = rows[a]["time"], _end_time(rows[b]["time"])
        out.append(_alert("dust", lvl, start, end, now, f"Mineral dust: up to {peak:.0f} µg/m³", f"Desert dust is forecast over the city {_when(start, end, now)}, peak {peak:.0f} µg/m³ around {rows[peak_i]['time'][11:16]}.",
                          peak={"value": round(peak, 1), "unit": "µg/m³", "time": rows[peak_i]["time"]}, cause=["dust"], source="Copernicus CAMS mineral dust forecast (via Open-Meteo)",
                          confidence="model", audience="sensitive" if lvl == "watch" else "everyone"))
    return out


# ---- fire -----------------------------------------------------------------------------------------------------------------------------------
def fire_alert(threat: dict | None, now: str) -> dict | None:
    if not threat:
        return None
    start = now[:13] + ":00"
    end = (datetime.strptime(start, "%Y-%m-%dT%H:%M") + timedelta(hours=int(threat["arrival_h"]) + 12)).strftime("%Y-%m-%dT%H:%M")
    where = f"{threat['count']} fire detections about {threat['nearest_km']:.0f} km {threat['direction']} of Sofia"
    return _alert("fire", "watch", start, end, now, "Smoke may reach the city", f"{where.capitalize()}, upwind in the 850 hPa flow (about {threat['arrival_h']:.0f} h away). Indicative: this says smoke may arrive, not how much.",
                  peak=None, cause=["fire"], source="NASA FIRMS VIIRS active fires + Open-Meteo 850 hPa wind", confidence="inferred", audience="sensitive")


def _annotate_causes(alerts: list[dict]) -> None:
    """The PM alert is the headline; say if a dust or fire alert overlaps it, so the cause is visible."""
    pm = next((a for a in alerts if a["kind"] == "pm"), None)
    if not pm:
        return
    for a in alerts:
        if a["kind"] in ("dust", "fire") and a["start"] < pm["end"] and a["end"] > pm["start"]:
            pm["cause"] = sorted(set(pm["cause"]) | set(a["cause"]))


def build_alerts(fc: dict, dust_series: list[dict] | None = None, fire_threat: dict | None = None, now: str | None = None) -> list[dict]:
    """All alerts for the forecast `fc` (backend.main.build_forecast), most severe first, then earliest."""
    hours = fc["hours"]
    now = now or fc.get("generated_at") or hours[fc["now_index"]]["time"]
    out = []
    if (a := pm_alert(fc, now)) is not None:
        out.append(a)
    out += heat_alerts(hours, fc["now_index"], now)
    out += dust_alerts(dust_series or [], now)
    if (a := fire_alert(fire_threat, now)) is not None:
        out.append(a)
    _annotate_causes(out)
    return sorted(out, key=lambda a: (-rank(a["level"]), a["start"]))
