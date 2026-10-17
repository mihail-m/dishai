"""Real traffic, as observed data only. Nothing here feeds an assumed number into the PM2.5 estimate.

Sources (all optional, keys in .env)
- Google Routes (GOOGLE_ROUTES_SERVER_KEY): travel time of ~12 arterial corridors (spokes out of the centre, picked from our own street
  network). `duration` at a FUTURE departure time is Google's typical traffic for that hour, so 2 x 24 requests per corridor give the
  typical hour-of-week profile; `duration` now is the live reading.
- TomTom (TOMTOM_API_KEY): Flow Segment Data (current vs free-flow travel time) on three points per corridor, incident details (jams,
  closures, roadworks) and the live flow raster tiles, proxied so the key never reaches the browser.

Index = travel time / quietest-hour travel time of the corridor (1.0 = free flow). Live vs typical = live index / typical index for this
hour. These are travel times, not vehicle counts, and they are not an input to the PM2.5 model: calibrating an effect would need traffic
history aligned with the sensor record, which we do not have. They are shown on the map and in the street popover.

Terms: Google content is kept in memory with a short TTL; only the derived hourly index (data/traffic_profile.json, no routes or
geometry) is saved. Check the Google Maps Platform terms before using it beyond a demo. Request counts are capped per day.
"""
import asyncio
import datetime as dt
import json
import logging
import os
import time
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from scipy.spatial import cKDTree

from . import google_routes as G
from .cai import config as C
from .cai.graph import DATA, Net, to_xy
from .cai.names import translit

log = logging.getLogger("inversion_watch")
TZ = ZoneInfo("Europe/Sofia")
PROFILE_PATH = DATA / "traffic_profile.json"
PROFILE_MAX_AGE_D = 7
CENTER = (23.3219, 42.6977)  # lon, lat
N_SPOKES, R_IN, R_OUT = 12, 1500.0, 6000.0
ASSIGN_M = 35.0  # street segments this close to a corridor's route belong to it
MAJOR = ["trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"]
GOOGLE_TTL, TOMTOM_TTL, INCIDENT_TTL, TILE_TTL = 900, 1800, 120, 180
BBOX = "{},{},{},{}".format(*C.BBOX)
INCIDENT_CATEGORY = {0: "Unknown", 1: "Accident", 2: "Fog", 3: "Dangerous conditions", 4: "Rain", 5: "Ice", 6: "Traffic jam",
                     7: "Lane closed", 8: "Road closed", 9: "Road works", 10: "Wind", 11: "Flooding", 14: "Broken-down vehicle"}
MAGNITUDE = {0: "unknown", 1: "minor", 2: "moderate", 3: "major", 4: "closure / undefined"}

_usage = {"day": "", "google": 0, "tomtom": 0}
_lock: asyncio.Lock | None = None
_profile: dict | None = None  # typical travel-time index by hour: {"wk": [24], "we": [24]} (mean of corridors)
_state: "State | None" = None
_cache: dict[str, tuple[float, object]] = {}


def google_key() -> str | None:
    return os.getenv("GOOGLE_ROUTES_SERVER_KEY") or None


def tomtom_key() -> str | None:
    return os.getenv("TOMTOM_API_KEY") or None


def enabled() -> bool:
    return not os.getenv("TRAFFIC_DISABLED") and bool(google_key() or tomtom_key())


def _spend(kind: str, n: int = 1) -> bool:
    today = dt.datetime.now(TZ).strftime("%Y-%m-%d")
    if _usage["day"] != today:
        _usage.update(day=today, google=0, tomtom=0)
    cap = int(os.getenv(f"TRAFFIC_{kind.upper()}_DAILY_MAX", 1500 if kind == "google" else 2300))
    if _usage[kind] + n > cap:
        return False
    _usage[kind] += n
    return True


def usage() -> dict:
    return {"day": _usage["day"], "google_requests": _usage["google"], "tomtom_requests": _usage["tomtom"]}


# --- corridors -----------------------------------------------------------------------------------------------------------


def pick_corridors(net: Net) -> list[dict]:
    """12 spokes: for each 30-degree wedge around the centre, an arterial street segment near 1.5 km and one near 6 km."""
    hw = np.array(net.hw_names)[net.e_hw]
    art = np.isin(hw, ["trunk", "primary", "secondary"])
    cx, cy = (float(v) for v in to_xy(*CENTER))
    dx, dy = net.mid_xy[:, 0] - cx, net.mid_xy[:, 1] - cy
    r = np.hypot(dx, dy)
    ang = np.degrees(np.arctan2(dy, dx)) % 360
    out = []
    for k in range(N_SPOKES):
        wedge = np.where(art & (((ang - k * 30) % 360) < 30))[0]
        if not len(wedge):
            continue
        a = wedge[np.argmin(np.abs(r[wedge] - R_IN))]
        b = wedge[np.argmin(np.abs(r[wedge] - R_OUT))]
        if abs(r[a] - R_IN) > 1200 or abs(r[b] - R_OUT) > 1800 or a == b:
            continue
        nm = int(net.e_name[b]) if net.e_name[b] >= 0 else int(net.e_name[a])
        dirs = ["E", "ENE", "NNE", "N", "NNW", "WNW", "W", "WSW", "SSW", "S", "SSE", "ESE"]
        label = f"{translit(net.names[nm])} ({dirs[k]})" if nm >= 0 else f"Corridor {dirs[k]}"
        out.append({"id": f"c{k:02d}", "name": label,
                    "o": (float(net.e_mid_lat[a]), float(net.e_mid_lon[a])), "d": (float(net.e_mid_lat[b]), float(net.e_mid_lon[b]))})
    return out


def _densify_xy(lat_lng: list[tuple[float, float]], step: float = 15.0) -> np.ndarray:
    lat = np.array([p[0] for p in lat_lng])
    lng = np.array([p[1] for p in lat_lng])
    x, y = to_xy(lng, lat)
    pts = [np.column_stack([x, y])[:1]]
    for i in range(1, len(x)):
        seg = np.hypot(x[i] - x[i - 1], y[i] - y[i - 1])
        n = max(1, int(seg // step))
        t = (np.arange(1, n + 1) / n)[:, None]
        pts.append(np.array([[x[i - 1], y[i - 1]]]) * (1 - t) + np.array([[x[i], y[i]]]) * t)
    return np.vstack(pts)


def assign_edges(net: Net, lat_lng: list[tuple[float, float]]) -> np.ndarray:
    """Indices of major-road directed edges lying within ASSIGN_M of the route."""
    cand = np.where(net.hw_mask(*MAJOR))[0]
    d, _ = cKDTree(_densify_xy(lat_lng)).query(net.mid_xy[cand])
    return cand[d <= ASSIGN_M]


# --- Google --------------------------------------------------------------------------------------------------------------


def _loc(p):
    return {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}


async def _google_route(client: httpx.AsyncClient, o, d, depart: dt.datetime, *, shape: bool = False) -> dict | None:
    key = google_key()
    if not key or not _spend("google"):
        return None
    body = {"origin": _loc(o), "destination": _loc(d), "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "departureTime": depart.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    mask = "routes.duration,routes.distanceMeters"
    if shape:
        body["extraComputations"] = ["TRAFFIC_ON_POLYLINE"]
        mask += ",routes.polyline.encodedPolyline,routes.travelAdvisory.speedReadingIntervals"
    try:
        r = await client.post(G.URL, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": mask})
        r.raise_for_status()
        rt = r.json()["routes"][0]
    except Exception as exc:  # noqa: BLE001 - never log the URL/headers (key)
        log.warning("Google traffic call failed: %s", type(exc).__name__)
        return None
    out = {"duration_s": float(str(rt["duration"]).rstrip("s")), "distance_m": float(rt.get("distanceMeters", 0))}
    if shape:
        poly = G.decode_polyline(rt["polyline"]["encodedPolyline"])
        out["polyline"] = poly
        iv = rt.get("travelAdvisory", {}).get("speedReadingIntervals", [])
        n = max(len(poly) - 1, 1)
        slow = sum((i.get("endPolylinePointIndex", 0) - i.get("startPolylinePointIndex", 0)) for i in iv if i.get("speed") in ("SLOW", "TRAFFIC_JAM"))
        jam = sum((i.get("endPolylinePointIndex", 0) - i.get("startPolylinePointIndex", 0)) for i in iv if i.get("speed") == "TRAFFIC_JAM")
        out["slow_share"], out["jam_share"] = round(slow / n, 3), round(jam / n, 3)
    return out


def _next_weekday(start: dt.date, weekday: int) -> dt.date:
    return start + dt.timedelta(days=(weekday - start.weekday()) % 7 or 7)


async def _profile_for(client, corridors: list[dict], sem: asyncio.Semaphore) -> dict[str, dict]:
    """24 weekday (Tuesday) and 24 weekend (Sunday) typical travel times per corridor."""
    today = dt.datetime.now(TZ).date()
    days = {"wk": _next_weekday(today, 1), "we": _next_weekday(today, 6)}

    async def one(c, kind, h):
        async with sem:
            t = dt.datetime.combine(days[kind], dt.time(h), tzinfo=TZ)
            rt = await _google_route(client, c["o"], c["d"], t)
            return c["id"], kind, h, (rt["duration_s"] if rt else None)

    res = await asyncio.gather(*[one(c, k, h) for c in corridors for k in ("wk", "we") for h in range(24)])
    out: dict[str, dict] = {c["id"]: {"wk": [None] * 24, "we": [None] * 24} for c in corridors}
    for cid, kind, h, v in res:
        out[cid][kind][h] = v
    return out


def _load_profile_file() -> dict | None:
    try:
        d = json.loads(PROFILE_PATH.read_text())
        built = dt.date.fromisoformat(d["built"])
        if (dt.datetime.now(TZ).date() - built).days > PROFILE_MAX_AGE_D:
            return None
        return d
    except Exception:  # noqa: BLE001 - missing or corrupt file just means "rebuild"
        return None


def load_saved_profile() -> bool:
    """Called at startup: use the saved measured profile right away, no network."""
    d = _load_profile_file()
    if not d:
        return False
    global _profile
    _profile = {"wk": d["tti_wk"], "we": d["tti_we"]}
    return True


# --- state ---------------------------------------------------------------------------------------------------------------


class State:
    def __init__(self, corridors: list[dict]):
        self.corridors = corridors  # each: id, name, o, d, polyline, edges, profile{wk,we,free_s}, google{}, tomtom{}
        self.updated = 0.0
        self.edge_corridor: tuple[int, np.ndarray] | None = None  # (id(net), corridor index per directed edge, -1 = none)


def _typical_tti(c: dict, now: dt.datetime) -> float | None:
    p = c.get("profile")
    if not p:
        return None
    arr = p["we" if now.weekday() >= 5 else "wk"]
    h, f = now.hour, now.minute / 60.0
    a, b = arr[h], arr[(h + 1) % 24]
    return a + (b - a) * f


def corridor_ratio(c: dict, now: dt.datetime) -> tuple[float | None, float | None, float | None]:
    """(live index, typical index, live/typical) for a corridor."""
    live = [v for v in (c.get("google", {}).get("tti"), c.get("tomtom", {}).get("tti")) if v]
    typ = _typical_tti(c, now)
    if not live:
        return None, typ, None
    li = float(np.mean(live))
    return li, typ, (li / typ if typ else None)


async def _tomtom_flow(client, c: dict) -> dict | None:
    key = tomtom_key()
    poly = c.get("polyline")
    if not key or not poly:
        return None
    idx = [len(poly) // 4, len(poly) // 2, 3 * len(poly) // 4]
    cur = free = 0.0
    n_ok = 0
    for i in idx:
        if not _spend("tomtom"):
            break
        try:
            r = await client.get("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/12/json",
                                 params={"key": key, "point": f"{poly[i][0]:.6f},{poly[i][1]:.6f}"})
            r.raise_for_status()
            s = r.json()["flowSegmentData"]
        except Exception as exc:  # noqa: BLE001
            log.warning("TomTom flow call failed: %s", type(exc).__name__)
            continue
        if s.get("roadClosure") or s.get("confidence", 0) < 0.5 or not s.get("freeFlowTravelTime"):
            continue
        cur += s["currentTravelTime"]
        free += s["freeFlowTravelTime"]
        n_ok += 1
    if not n_ok:
        return None
    return {"tti": round(cur / free, 3), "points": n_ok, "at": time.time()}


_task: "asyncio.Task | None" = None


def kick(net: Net) -> None:
    """Start a background refresh unless one is running or the last one is fresh. Never blocks the caller."""
    global _task
    if not enabled() or (_task and not _task.done()):
        return
    if _state is not None and _state.updated and time.time() - _state.updated < GOOGLE_TTL:
        return
    try:
        _task = asyncio.get_running_loop().create_task(ensure(net))
    except RuntimeError:  # no running loop (called from a worker thread): the next request will start it
        pass


async def ensure(net: Net) -> None:
    """Build (once) and refresh (when stale) the corridors, the hour-of-week profile and the live readings. Never raises."""
    global _state, _lock
    if not enabled():
        return
    _lock = _lock or asyncio.Lock()
    async with _lock:
        try:
            await _ensure(net)
        except Exception as exc:  # noqa: BLE001 - traffic must never take the app down
            log.warning("traffic refresh failed: %s", type(exc).__name__)


async def _ensure(net: Net) -> None:
    global _state
    now_t = time.time()
    async with httpx.AsyncClient(timeout=15) as client:
        if _state is None:
            corridors = await asyncio.to_thread(pick_corridors, net)
            sem = asyncio.Semaphore(8)

            async def shape(c):
                async with sem:
                    return await _google_route(client, c["o"], c["d"], dt.datetime.now(TZ) + dt.timedelta(seconds=30), shape=True)

            if google_key():
                shapes = await asyncio.gather(*[shape(c) for c in corridors])
                keep = []
                for c, s in zip(corridors, shapes):
                    if not s or not 1500 <= s["distance_m"] <= 15000:
                        continue  # failed, or Google routed somewhere that is not a plausible spoke
                    c["polyline"] = s["polyline"]
                    c["distance_m"] = s["distance_m"]
                    c["edges"] = await asyncio.to_thread(assign_edges, net, s["polyline"])
                    c["google"] = {"duration_s": s["duration_s"], "slow_share": s["slow_share"], "jam_share": s["jam_share"], "at": now_t}
                    keep.append(c)
                corridors = keep
            _state = State(corridors)
            await _attach_profile(client, net)
        stale_g = now_t - max([c.get("google", {}).get("at", 0) for c in _state.corridors] or [0]) > GOOGLE_TTL
        stale_t = now_t - max([c.get("tomtom", {}).get("at", 0) for c in _state.corridors] or [0]) > TOMTOM_TTL
        if stale_g and google_key():
            sem = asyncio.Semaphore(8)

            async def live(c):
                async with sem:
                    return await _google_route(client, c["o"], c["d"], dt.datetime.now(TZ) + dt.timedelta(seconds=30), shape=True)

            res = await asyncio.gather(*[live(c) for c in _state.corridors])
            for c, s in zip(_state.corridors, res):
                if s:
                    free = (c.get("profile") or {}).get("free_s")
                    c["google"] = {"duration_s": s["duration_s"], "slow_share": s["slow_share"], "jam_share": s["jam_share"],
                                   "tti": round(s["duration_s"] / free, 3) if free else None, "at": now_t}
        if stale_t and tomtom_key():
            res = await asyncio.gather(*[_tomtom_flow(client, c) for c in _state.corridors])
            for c, t in zip(_state.corridors, res):
                if t:
                    c["tomtom"] = t
        _state.updated = time.time()


async def _attach_profile(client, net: Net) -> None:
    """Per-corridor 48-hour typical profile: from the saved file when valid, else from Google (about 12 x 48 requests)."""
    saved = _load_profile_file()
    by_id = {c["id"]: c for c in _state.corridors}
    prof = None
    if saved and set(saved.get("corridors", {})) >= set(by_id):
        prof = {cid: saved["corridors"][cid] for cid in by_id}
    elif google_key() and _state.corridors:
        raw = await _profile_for(client, _state.corridors, asyncio.Semaphore(8))
        prof = {}
        for cid, v in raw.items():
            vals = [x for x in v["wk"] + v["we"] if x]
            if len(vals) < 40:
                continue  # incomplete (quota or errors): do not guess
            free = min(vals)
            prof[cid] = {"free_s": free, "wk": [round((x or free) / free, 3) for x in v["wk"]],
                         "we": [round((x or free) / free, 3) for x in v["we"]]}
    if not prof:
        return
    for cid, c in by_id.items():
        if cid in prof:
            c["profile"] = prof[cid]
            g = c.get("google", {})
            if g.get("duration_s") and prof[cid].get("free_s"):
                g["tti"] = round(g["duration_s"] / prof[cid]["free_s"], 3)
    good = [c["profile"] for c in by_id.values() if c.get("profile")]
    if not good:
        return
    wk = [float(np.mean([p["wk"][h] for p in good])) for h in range(24)]
    we = [float(np.mean([p["we"][h] for p in good])) for h in range(24)]
    global _profile
    _profile = {"wk": [round(x, 3) for x in wk], "we": [round(x, 3) for x in we]}
    if not saved or not set(saved.get("corridors", {})) >= set(by_id):
        try:
            PROFILE_PATH.write_text(json.dumps({
                "built": dt.datetime.now(TZ).date().isoformat(), "source": "Google Routes API typical travel time by hour (Tue / Sun)",
                "corridors": {cid: {"name": by_id[cid]["name"], **prof[cid]} for cid in prof}, "tti_wk": [round(x, 3) for x in wk], "tti_we": [round(x, 3) for x in we]}))
        except OSError as exc:
            log.warning("could not save the traffic profile: %s", type(exc).__name__)


# --- model hooks ---------------------------------------------------------------------------------------------------------


def edge_reading(net: Net, e: int, now: dt.datetime | None = None) -> dict | None:
    """What was measured on this street segment: the corridor whose route runs along it (within ASSIGN_M), or None. Streets between the
    corridors have no traffic measurement and get none."""
    st = _state
    if st is None or not st.corridors:
        return None
    if st.edge_corridor is None or st.edge_corridor[0] != id(net):
        owner = np.full(net.n_edges, -1, dtype=np.int32)
        for k, c in enumerate(st.corridors):
            owner[c.get("edges", [])] = k
        st.edge_corridor = (id(net), owner)
    k = int(st.edge_corridor[1][e])
    if k < 0:
        return None
    c = st.corridors[k]
    live, typ, ratio = corridor_ratio(c, now or dt.datetime.now(TZ))
    if live is None:
        return None
    return {"corridor": c["name"], "live_index": round(live, 2), "typical_index": round(typ, 2) if typ else None,
            "vs_typical": round(ratio, 2) if ratio else None,
            "google_min": round(c["google"]["duration_s"] / 60, 1) if c.get("google", {}).get("duration_s") else None,
            "tomtom_index": c.get("tomtom", {}).get("tti"), "jam_share": c.get("google", {}).get("jam_share")}


def snapshot() -> dict:
    now = dt.datetime.now(TZ)
    rows = []
    for c in (_state.corridors if _state else []):
        li, typ, ratio = corridor_ratio(c, now)
        rows.append({
            "id": c["id"], "name": c["name"], "polyline": [[round(p[1], 5), round(p[0], 5)] for p in c.get("polyline", [])],
            "length_km": round(c.get("distance_m", 0) / 1000, 1),
            "live_index": round(li, 2) if li else None, "typical_index": round(typ, 2) if typ else None,
            "vs_typical": round(ratio, 2) if ratio else None,
            "google": {k: v for k, v in c.get("google", {}).items() if k != "at"},
            "tomtom": {k: v for k, v in c.get("tomtom", {}).items() if k != "at"},
        })
    return {
        "enabled": enabled(), "google": bool(google_key()), "tomtom": bool(tomtom_key()),
        "ready": bool(_state and _state.corridors), "measured_profile": bool(_profile),
        "updated": dt.datetime.fromtimestamp(_state.updated, TZ).isoformat(timespec="seconds") if _state and _state.updated else None,
        "corridors": rows, "profile": _profile, "usage": usage(),
        "note": "Index = travel time / quietest-hour travel time (1.0 = free flow); vs typical = live index / typical index for this hour. "
                "Measured on arterial corridors only. Observed data: it is not an input to the PM2.5 estimate.",
        "sources": ["Google Routes API (typical and live travel time)", "TomTom Flow Segment Data (current vs free-flow)"],
    }


# --- TomTom incidents and tiles --------------------------------------------------------------------------------------------


async def incidents() -> dict:
    key = tomtom_key()
    hit = _cache.get("incidents")
    if hit and time.time() - hit[0] < INCIDENT_TTL:
        return hit[1]
    if not key or not _spend("tomtom"):
        return {"type": "FeatureCollection", "features": [], "enabled": bool(key)}
    fields = ("{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,events{description,iconCategory},"
              "startTime,endTime,from,to,length,delay,roadNumbers,probabilityOfOccurrence,numberOfReports,lastReportTime}}}")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.tomtom.com/traffic/services/5/incidentDetails",
                            params={"key": key, "bbox": BBOX, "fields": fields, "language": "en-GB", "timeValidityFilter": "present"})
            r.raise_for_status()
            raw = r.json().get("incidents", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("TomTom incidents failed: %s", type(exc).__name__)
        return hit[1] if hit else {"type": "FeatureCollection", "features": [], "enabled": True, "error": True}
    feats = []
    for f in raw:
        p = f.get("properties", {})
        cat = p.get("iconCategory", 0)
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "id": p.get("id"), "category": INCIDENT_CATEGORY.get(cat, "Incident"), "category_code": cat,
            "magnitude": MAGNITUDE.get(p.get("magnitudeOfDelay", 0), "unknown"), "magnitude_code": p.get("magnitudeOfDelay", 0),
            "description": "; ".join(e.get("description", "") for e in p.get("events", []) if e.get("description")),
            "from": p.get("from"), "to": p.get("to"), "length_m": round(p.get("length") or 0), "delay_s": p.get("delay"),
            "road": ", ".join(p.get("roadNumbers") or []), "start": p.get("startTime"), "end": p.get("endTime"),
            "reports": p.get("numberOfReports")}})
    out = {"type": "FeatureCollection", "features": feats, "enabled": True, "count": len(feats),
           "source": "TomTom Traffic Incidents", "updated": dt.datetime.now(TZ).isoformat(timespec="seconds")}
    _cache["incidents"] = (time.time(), out)
    return out


async def flow_tile(z: int, x: int, y: int) -> tuple[bytes, str] | None:
    """TomTom raster flow tile (only delays are drawn, transparent elsewhere), proxied so the key stays on the server."""
    key = tomtom_key()
    if not key or not (8 <= z <= 18):
        return None
    ck = f"tile/{z}/{x}/{y}"
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < TILE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.tomtom.com/traffic/map/4/tile/flow/relative-delay/{z}/{x}/{y}.png", params={"key": key})
            r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("TomTom tile failed: %s", type(exc).__name__)
        return hit[1] if hit else None
    out = (r.content, r.headers.get("content-type", "image/png"))
    _cache[ck] = (time.time(), out)
    if len(_cache) > 600:
        for k in [k for k in _cache if k.startswith("tile/")][:200]:
            _cache.pop(k, None)
    return out
