"""Google Routes API client (server side). Optional: everything works without a key.

- Key from GOOGLE_ROUTES_SERVER_KEY only; it is sent in a header, never logged, never returned.
- X-Goog-FieldMask asks for just the polyline, duration and distance.
- No alternative routes (unsupported together with waypoints).
- Responses are kept only in a short in-memory session cache (Google terms): nothing is persisted.
"""
import hashlib
import json
import logging
import os
import time

import httpx

log = logging.getLogger("inversion_watch")

URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"
SESSION_TTL = 600
MAX_INTERMEDIATES = 8  # up to 10 stay in the cheapest billing tier; keep margin

_session: dict[str, tuple[float, dict]] = {}


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Google encoded polyline (precision 5) -> [(lat, lng)]."""
    pts, i, lat, lng = [], 0, 0, 0
    while i < len(encoded):
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        pts.append((lat / 1e5, lng / 1e5))
    return pts


def encode_polyline(points: list[tuple[float, float]]) -> str:
    out, plat, plng = [], 0, 0
    for lat, lng in points:
        for v, prev in ((round(lat * 1e5), plat), (round(lng * 1e5), plng)):
            d = v - prev
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1F)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plng = round(lat * 1e5), round(lng * 1e5)
    return "".join(out)


def enabled() -> bool:
    return bool(os.getenv("GOOGLE_ROUTES_SERVER_KEY"))


def maps_url(origin: tuple[float, float], dest: tuple[float, float], waypoints: list[tuple[float, float]] | None = None) -> str:
    """Open-in-Google-Maps deep link (no key needed). Points are (lat, lng)."""
    q = f"https://www.google.com/maps/dir/?api=1&travelmode=walking&origin={origin[0]:.6f},{origin[1]:.6f}&destination={dest[0]:.6f},{dest[1]:.6f}"
    if waypoints:
        q += "&waypoints=" + "%7C".join(f"{a:.6f},{b:.6f}" for a, b in waypoints[:MAX_INTERMEDIATES])
    return q


def _loc(p: tuple[float, float]) -> dict:
    return {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}


async def walk(origin: tuple[float, float], dest: tuple[float, float], intermediates: list[tuple[float, float]] | None = None) -> dict | None:
    """Walking route via Routes API. Points are (lat, lng). Returns None if disabled or on any error."""
    key = os.getenv("GOOGLE_ROUTES_SERVER_KEY")
    if not key:
        return None
    body = {"origin": _loc(origin), "destination": _loc(dest), "travelMode": "WALK", "units": "METRIC"}
    if intermediates:
        body["intermediates"] = [_loc(p) for p in intermediates[:MAX_INTERMEDIATES]]
    ck = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    hit = _session.get(ck)
    if hit and time.time() - hit[0] < SESSION_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(URL, json=body, headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK})
            r.raise_for_status()
        route = r.json()["routes"][0]
        out = {
            "polyline": decode_polyline(route["polyline"]["encodedPolyline"]),
            "duration_s": float(str(route["duration"]).rstrip("s")),
            "distance_m": float(route.get("distanceMeters", 0)),
        }
    except Exception as exc:  # noqa: BLE001 - never include headers/URL (key) in logs
        log.warning("Google Routes call failed: %s", type(exc).__name__)
        return None
    _session[ck] = (time.time(), out)
    if len(_session) > 200:
        _session.pop(next(iter(_session)))
    return out
