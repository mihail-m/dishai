"""One list of things the user should know about in the next 48 hours: pollution episodes, heat, mineral dust and smoke from fires.

Every kind answers the same questions (what, when, how bad, how sure, what to do), so the banner, the suggestions and the push
notifications all read the same objects instead of each re-deriving them. The alert id is stable (kind + start day), the level can escalate;
a notification is sent once per (id, level).

Sources, and what each can honestly claim:
  pm    the city PM2.5 episode forecast (backend/alert.py): the learned model, back-tested in docs/PM_MODEL.md. CAMS, which feeds that model,
        already carries fire and dust emissions, so a smoke or dust episode raises this alert too; the other kinds below say WHY.
  heat  the city feels-like forecast against the advice bands (backend/advice.py HEAT_BANDS).
  dust  CAMS mineral dust (Copernicus Atmosphere Monitoring Service, via Open-Meteo, no key), taken as published: the level comes from the dust forecast
        itself (thresholds from the history of the field); the message adds CAMS's own daily PM10 and the dust share of it (backend/plumes.py).
  fire  active-fire detections (NASA FIRMS, VIIRS, keyless regional files) near or upwind of Sofia at 850 hPa. Says smoke may arrive, not how much; it
        becomes "likely" when CAMS also shows carbon monoxide or PM2.5 well above the usual for the month, and a warning when CAMS PM2.5 reaches the
        PM2.5 warning level. (The EFFIS / GWIS web services were returning server errors when checked, so they are not used.)
  transport  a suggestion, not a hazard: leave the car at home on a day CAMS forecasts worse than usual for the month, and the Sofia "green ticket" when
        the municipality has announced one (backend/green_ticket.py; no feed exists, so announcements are entered by a person).
Tested and dropped: a "smoke" rule from CAMS aerosol optical depth and carbon monoxide. It fired 69 times in 21 months, almost all ordinary haze.
"""
from datetime import datetime, timedelta

from .advice import HEAT_BANDS
from .cai import config as C

LEVELS = ("info", "watch", "warning")
KINDS = ("pm", "heat", "dust", "fire", "transport")
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


# A pollution episode needs a different answer depending on what it is made of (backend/plumes.py classifies it).
PM_ACTIONS_BY_CAUSE = {
    "fire": [("windows", "Keep windows and doors closed", "Smoke is fine particles that pass through gaps; seal what you can."),
             ("purifier", "Run a HEPA air purifier", "In the room you sleep or work in. Turn off fans that draw outdoor air."),
             ("mask", "FFP2 / N95 mask if you must go out", "Surgical masks and cloth masks do not stop smoke."),
             ("exercise", "Skip outdoor exercise", "Smoke is most harmful while breathing hard."),
             ("burn", "Do not add smoke yourself", "No wood stoves, barbecues, or burning leaves and waste.")],
    "dust": [("windows", "Close windows facing the wind", "Dust is coarse: it settles on surfaces and irritates eyes, nose and airways."),
             ("clean", "Wet-clean surfaces; do not dry-sweep", "Dry cleaning puts it back in the air."),
             ("sensitive", "Asthma or hay fever: keep reliever medication to hand", "Wear glasses rather than contact lenses outdoors."),
             ("car", "Recirculate air in the car", "Dust clogs filters: replace the cabin filter if it is dirty.")],
    "local": [("burn", "Do not burn wood, coal or waste", "Burning adds straight to the episode; district heating or electricity does not."),
              ("car", "Leave the car if you can", "Traffic adds to it on the streets that are already still and cold."),
              ("airing", "Air the home in the afternoon", "When the air is deepest, usually 13:00-16:00; avoid the cold still hours at night."),
              ("windows", "Keep windows closed in the evening and at night", "This is when trapped air is worst."),
              ("sensitive", "Sensitive groups: keep trips short and away from busy streets", "")],
}


def _alert(kind: str, level: str, start: str, end: str, now: str, title: str, message: str, *, peak: dict | None = None, cause: list[str] | None = None,
           source: str, confidence: str, audience: str = "everyone", detail: dict | None = None) -> dict:
    acts = ACTIONS.get(kind, {}).get(level) or ACTIONS.get(kind, {}).get("watch", [])
    out = {
        "id": f"{kind}-{start[:10]}", "kind": kind, "level": level, "title": title, "message": message, "start": start, "end": end,
        "in_progress": start <= now < end, "peak": peak, "cause": cause or [], "source": source, "confidence": confidence, "audience": audience,
        "actions": [{"id": i, "title": t, "body": b} for i, t, b in acts],
    }
    if detail:
        out["detail"] = detail
    return out


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
def dust_alerts(series: list[dict], now: str, outlook: list[dict] | None = None) -> list[dict]:
    """series: [{time, dust}] from CAMS. One alert per run of at least DUST_MIN_HOURS hours at or above the watch threshold.
    outlook (backend/plumes.outlook) adds CAMS's own daily PM10 for the day of the peak and the share of it that is dust."""
    rows = [r for r in series if r["time"] >= now[:13] + ":00"][:HORIZON_H]
    vals = [r.get("dust") if r.get("dust") is not None else 0.0 for r in rows]
    days = {d["date"]: d for d in (outlook or [])}
    out = []
    for a, b in _runs([v >= DUST_WATCH for v in vals]):
        if b - a + 1 < DUST_MIN_HOURS:
            continue
        peak_i = max(range(a, b + 1), key=lambda k: vals[k])
        peak = vals[peak_i]
        lvl = "warning" if peak >= DUST_WARNING else "watch"
        start, end = rows[a]["time"], _end_time(rows[b]["time"])
        day = days.get(rows[peak_i]["time"][:10])
        extra, detail = "", None
        if day:
            share = f", {day['dust_share']:.0%} of it dust" if day.get("dust_share") is not None else ""
            usual = f" (a usual {datetime.strptime(day['date'], '%Y-%m-%d').strftime('%B')} day: about {day['usual_pm10']:.0f})" if day.get("usual_pm10") else ""
            extra = f" CAMS puts that day's PM10 at about {day['pm10']:.0f} µg/m³{usual}{share}."
            detail = {"date": day["date"], "pm10": day["pm10"], "usual_pm10": day.get("usual_pm10"), "dust_share": day.get("dust_share")}
        out.append(_alert("dust", lvl, start, end, now, f"Mineral dust: up to {peak:.0f} µg/m³", f"Desert dust is forecast over the city {_when(start, end, now)}, peak {peak:.0f} µg/m³ around {rows[peak_i]['time'][11:16]}.{extra}",
                          peak={"value": round(peak, 1), "unit": "µg/m³", "time": rows[peak_i]["time"]}, cause=["dust"], source="Copernicus CAMS mineral dust forecast (via Open-Meteo)",
                          confidence="model", audience="sensitive" if lvl == "watch" else "everyone", detail=detail))
    return out


# ---- fire -----------------------------------------------------------------------------------------------------------------------------------
def fire_alert(threat: dict | None, now: str, plume: dict | None = None) -> dict | None:
    """plume: backend/plumes.classify. CAMS confirming the smoke (CO or PM2.5 unusually high for the month) makes it "likely"; reaching the PM2.5 warning level, a warning."""
    if not threat:
        return None
    start = now[:13] + ":00"
    hours = int(threat["arrival_h"]) + 12
    end = (datetime.strptime(start, "%Y-%m-%dT%H:%M") + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
    sure = bool(plume and "smoke" in plume.get("components", []))
    pk = (plume or {}).get("pm25_peak_3h")
    lvl = "warning" if sure and pk is not None and pk >= C.PM_WARNING else "watch"
    near = threat.get("near")
    where = f"{threat['count']} fire detections {'within' if near else 'about'} {threat['nearest_km']:.0f} km {threat['direction']} of Sofia"
    how = "close to the city" if near else f"upwind in the 850 hPa flow (about {threat['arrival_h']:.0f} h away)"
    if sure:
        title = "Smoke is likely over the city" if lvl == "watch" else "Smoke is raising fine particles"
        msg = f"{(where[0].upper() + where[1:])}, {how}. CAMS also shows carbon monoxide or PM2.5 unusually high for the season" + (f" (PM2.5 around {pk:.0f} µg/m³)." if pk is not None else ".")
    else:
        title = "Smoke may reach the city"
        msg = f"{(where[0].upper() + where[1:])}, {how}. Indicative: this says smoke may arrive, not how much."
    return _alert("fire", lvl, start, end, now, title, msg, peak={"value": pk, "unit": "µg/m³", "time": None} if sure and pk is not None else None, cause=["fire"],
                  source="NASA FIRMS VIIRS active fires + Open-Meteo 850 hPa wind" + (" + Copernicus CAMS" if sure else ""), confidence="model" if sure else "inferred", audience="sensitive" if lvl == "watch" else "everyone",
                  detail={"detections": threat["count"], "frp_mw": threat["frp_mw"], "nearest_km": threat["nearest_km"], "direction": threat["direction"], "arrival_h": threat["arrival_h"],
                          "cams_confirms": sure, "near": bool(near)})


# ---- transport ------------------------------------------------------------------------------------------------------------------------------
TRANSPORT_ACTIONS = [("transit", "Take the metro, tram or bus", "Fewer cars on the street means less exhaust and less dust thrown back into the air."),
                     ("park", "If you must drive, park at the edge and ride in", "Metro stations with car parks make that easy."),
                     ("burn", "Keep wood and waste fires out of it too", "")]


ACTIONS["transport"] = {"watch": TRANSPORT_ACTIONS, "info": TRANSPORT_ACTIONS}


def _day_word(day: str, today: str) -> str:
    d, t = datetime.strptime(day, "%Y-%m-%d"), datetime.strptime(today, "%Y-%m-%d")
    return "today" if d == t else "tomorrow" if d == t + timedelta(days=1) else d.strftime("%A")


def transport_alerts(days: list[dict], tickets: list[dict], now: str) -> list[dict]:
    """Green ticket announced by the municipality (watch), else one suggestion for the first worse-than-usual day among today and tomorrow (info).
    days: backend/plumes.outlook; tickets: backend/green_ticket.announced."""
    today = now[:10]
    out, ticket_days = [], {t["date"] for t in tickets}
    for t in tickets:
        w = _day_word(t["date"], today)
        note = f" {t['note']}" if t.get("note") else ""
        out.append(_alert("transport", "watch", f"{t['date']}T00:00", _end_time(f"{t['date']}T23:00"), now, f"Green ticket {w}: ride public transport",
                          f"Sofia Municipality has announced the green ticket (a reduced-price all-day public-transport ticket) for {w}.{note} The most direct thing you can do for the air is to leave the car at home.",
                          source=("Sofia Municipality announcement: " + t["source"]) if t.get("source") else "Sofia Municipality announcement (entered by hand)", confidence="forecast", detail={"green_ticket": True, "source_url": t.get("source") or None}))
    late = int(now[11:13]) >= 17
    for d in days[:2]:
        if not d["worse_than_usual"] or d["date"] in ticket_days or (d["date"] == today and late):
            continue
        w = _day_word(d["date"], today)
        month = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%B")
        if d["worse_by"] == "pm25":
            what = f"PM2.5 about {d['pm25']:.0f} µg/m³ (a usual {month} day: {d['usual_pm25']:.0f})"
        else:
            what = f"PM10 about {d['pm10']:.0f} µg/m³ (a usual {month} day: {d['usual_pm10']:.0f})"
        out.append(_alert("transport", "info", f"{d['date']}T00:00", _end_time(f"{d['date']}T23:00"), now, f"Air worse than usual {w}: use public transport",
                          f"CAMS forecasts {what}, worse than a usual day. Leaving the car at home helps. Sofia sometimes announces a green ticket on days like this; none is announced yet, so check the municipality.",
                          source="Copernicus CAMS forecast compared with its own history for the month", confidence="model", audience="everyone", detail={"green_ticket": False}))
        break
    return out


def _annotate_causes(alerts: list[dict], plume: dict | None = None) -> None:
    """The PM alert is the headline; say if a dust or fire alert overlaps it (or the episode is local winter pollution), so the cause is visible,
    and swap the generic advice for the advice that fits a single cause."""
    pm = next((a for a in alerts if a["kind"] == "pm"), None)
    if not pm:
        return
    for a in alerts:
        if a["kind"] in ("dust", "fire") and a["start"] < pm["end"] and a["end"] > pm["start"]:
            pm["cause"] = sorted(set(pm["cause"]) | set(a["cause"]))
    if not pm["cause"] and plume and plume.get("kind") == "local":
        pm["cause"] = ["local"]
    if len(pm["cause"]) == 1 and pm["cause"][0] in PM_ACTIONS_BY_CAUSE:
        pm["actions"] = [{"id": i, "title": t, "body": b} for i, t, b in PM_ACTIONS_BY_CAUSE[pm["cause"][0]]]


def build_alerts(fc: dict, dust_series: list[dict] | None = None, fire_threat: dict | None = None, now: str | None = None, plume: dict | None = None,
                 tickets: list[dict] | None = None) -> list[dict]:
    """All alerts for the forecast `fc` (backend.main.build_forecast), most severe first, then earliest.
    plume: backend.plumes.assess output {days, plume, ...}; without it the dust and fire alerts work from CAMS dust and the fire rule alone."""
    hours = fc["hours"]
    now = now or fc.get("generated_at") or hours[fc["now_index"]]["time"]
    out = []
    if (a := pm_alert(fc, now)) is not None:
        out.append(a)
    out += heat_alerts(hours, fc["now_index"], now)
    out += dust_alerts(dust_series or [], now, (plume or {}).get("days"))
    if (a := fire_alert(fire_threat, now, (plume or {}).get("plume"))) is not None:
        out.append(a)
    out += transport_alerts((plume or {}).get("days") or [], tickets or [], now)
    _annotate_causes(out, (plume or {}).get("plume"))
    return sorted(out, key=lambda a: (-rank(a["level"]), a["start"]))
