"""Active fires around Sofia, from NASA FIRMS (VIIRS 375 m, near real time) and the 850 hPa wind from Open-Meteo. No key needed.

Data: the FIRMS "Europe" regional files of the last 24 h from the three VIIRS satellites (Suomi NPP, NOAA-20, NOAA-21), which cover the Balkans,
Greece, western Turkey and Ukraine. A fire seen by two satellites is counted once (0.01 degree cell, the larger radiative power kept).

Rule (indicative, not a dispersion model): a detection counts when it is within MAX_KM of Sofia, its bearing from Sofia is within +-SECTOR_DEG of the
direction the 850 hPa wind blows FROM (24 h mean vector), and the air would cover the distance within MAX_TRAVEL_H at that wind speed. Fires within
NEAR_KM count whatever the wind does: smoke from a fire that close reaches the city on the first breeze and in still air it simply stays.
An alert needs MIN_DETECTIONS qualifying detections or MIN_FRP_MW of fire radiative power. Nothing here is validated against smoke measured in
Sofia (the FIRMS archive that would be needed to do so requires a key); docs/ALERTS.md says so.
"""
import math
from datetime import datetime

LAT0, LON0 = 42.6977, 23.3219
BBOX = (14.0, 36.0, 32.0, 48.0)  # west, south, east, north: the Balkans and surroundings
FIRMS_FILES = ("suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_Europe_24h.csv", "noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_24h.csv", "noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_24h.csv")
FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/"
WIND_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=wind_speed_850hPa,wind_direction_850hPa"
            "&forecast_days=2&timezone=Europe%2FSofia&wind_speed_unit=kmh")
MAX_KM, SECTOR_DEG, MAX_TRAVEL_H, NEAR_KM = 500.0, 40.0, 30.0, 25.0
MIN_DETECTIONS, MIN_FRP_MW = 10, 200.0
MIN_WIND_KMH = 8.0
COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def merge_detections(*lists: list[dict]) -> list[dict]:
    """One detection per 0.01 degree cell (about 1 km) across satellites, keeping the strongest radiative power; inside BBOX only."""
    w, s, e, n = BBOX
    cell: dict[tuple[int, int], dict] = {}
    for lst in lists:
        for d in lst:
            if not (s <= d["lat"] <= n and w <= d["lon"] <= e):
                continue
            k = (round(d["lat"] * 100), round(d["lon"] * 100))
            if k not in cell or d["frp"] > cell[k]["frp"]:
                cell[k] = d
    return list(cell.values())


def parse_firms_csv(text: str) -> list[dict]:
    """FIRMS area CSV -> detections [{lat, lon, frp, conf}] (low-confidence detections are dropped)."""
    lines = [l for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2 or "latitude" not in lines[0]:
        return []
    head = lines[0].split(",")
    col = {k: head.index(k) for k in ("latitude", "longitude", "frp", "confidence") if k in head}
    out = []
    for l in lines[1:]:
        f = l.split(",")
        try:
            conf = f[col["confidence"]].strip().lower() if "confidence" in col else "n"
            if conf in ("l", "low") or (conf.isdigit() and int(conf) < 30):
                continue
            out.append({"lat": float(f[col["latitude"]]), "lon": float(f[col["longitude"]]), "frp": float(f[col["frp"]]) if "frp" in col else 0.0, "conf": conf})
        except (ValueError, IndexError, KeyError):
            continue
    return out


def distance_bearing(lat: float, lon: float) -> tuple[float, float]:
    """(km, bearing in degrees clockwise from north) of a point as seen from Sofia."""
    r = math.pi / 180
    dy = (lat - LAT0) * 111.2
    dx = (lon - LON0) * 111.2 * math.cos(LAT0 * r)
    return math.hypot(dx, dy), (math.degrees(math.atan2(dx, dy)) + 360) % 360


def mean_wind(speeds: list[float], dirs_from: list[float]) -> tuple[float, float] | None:
    """(speed km/h, direction the wind blows FROM in degrees) as the vector mean of the hourly values."""
    pairs = [(s, d) for s, d in zip(speeds, dirs_from) if s is not None and d is not None]
    if not pairs:
        return None
    u = sum(-s * math.sin(math.radians(d)) for s, d in pairs) / len(pairs)  # eastward component of the flow
    v = sum(-s * math.cos(math.radians(d)) for s, d in pairs) / len(pairs)
    speed = math.hypot(u, v)
    return speed, (math.degrees(math.atan2(-u, -v)) + 360) % 360


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def smoke_threat(detections: list[dict], wind: tuple[float, float] | None) -> dict | None:
    """The qualifying upwind detections summarised, or None when there are too few (or the air is nearly still)."""
    if not detections:
        return None
    speed, from_deg = wind if wind is not None else (0.0, None)
    moving = speed >= MIN_WIND_KMH
    hit = []
    for d in detections:
        km, bearing = distance_bearing(d["lat"], d["lon"])
        near = km <= NEAR_KM
        upwind = moving and km <= MAX_KM and _angle_diff(bearing, from_deg) <= SECTOR_DEG and km / speed <= MAX_TRAVEL_H
        if near or upwind:
            hit.append((km, bearing, d.get("frp") or 0.0))
    frp = sum(h[2] for h in hit)
    if len(hit) < MIN_DETECTIONS and frp < MIN_FRP_MW:
        return None
    nearest = min(hit, key=lambda h: h[0])
    where = COMPASS[int(((nearest[1] + 22.5) % 360) // 45)]  # where the nearest fire lies, seen from Sofia
    return {"count": len(hit), "frp_mw": round(frp), "nearest_km": round(nearest[0]), "direction": where,
            "arrival_h": round(nearest[0] / speed, 1) if moving and nearest[0] > NEAR_KM else 0.0, "wind_kmh": round(speed), "wind_from_deg": round(from_deg) if from_deg is not None else None,
            "near": nearest[0] <= NEAR_KM}


async def fetch_detections(client) -> list[dict]:
    """Merged 24 h detections of all three satellites; a satellite file that fails is skipped, all failing raises."""
    got, errors = [], 0
    for f in FIRMS_FILES:
        try:
            r = await client.get(FIRMS_BASE + f, timeout=25)
            r.raise_for_status()
            got.append(parse_firms_csv(r.text))
        except Exception:  # noqa: BLE001
            errors += 1
    if errors == len(FIRMS_FILES):
        raise RuntimeError("FIRMS unavailable")
    return merge_detections(*got)


async def fetch_wind(client, hours: int = 24) -> tuple[float, float] | None:
    r = await client.get(WIND_URL.format(lat=LAT0, lon=LON0), timeout=15)
    r.raise_for_status()
    h = r.json()["hourly"]
    now = datetime.now().strftime("%Y-%m-%dT%H:00")
    i = next((k for k, t in enumerate(h["time"]) if t >= now), 0)
    return mean_wind(h["wind_speed_850hPa"][i : i + hours], h["wind_direction_850hPa"][i : i + hours])
