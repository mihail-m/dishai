"""Active fires upwind of Sofia, from NASA FIRMS (VIIRS 375 m, near real time) and the 850 hPa wind from Open-Meteo.

Needs a free FIRMS MAP_KEY (https://firms.modaps.eosdis.nasa.gov/api/area/) in FIRMS_MAP_KEY. Without it `enabled()` is False and the alert
kind is absent. The FIRMS client was only exercised against recorded responses in the tests (no key was available), like the Google paths.

Rule (indicative, not a dispersion model): a detection counts when it is within MAX_KM of Sofia, its bearing from Sofia is within +-SECTOR_DEG of the
direction the 850 hPa wind blows FROM (24 h mean vector), and the air would cover the distance within MAX_TRAVEL_H at that wind speed.
An alert needs MIN_DETECTIONS qualifying detections or MIN_FRP_MW of fire radiative power.
"""
import math
import os
from datetime import datetime

LAT0, LON0 = 42.6977, 23.3219
BBOX = (14.0, 36.0, 32.0, 48.0)  # west, south, east, north: the Balkans and surroundings
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/{w},{s},{e},{n}/2"
WIND_URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=wind_speed_850hPa,wind_direction_850hPa"
            "&forecast_days=2&timezone=Europe%2FSofia&wind_speed_unit=kmh")
MAX_KM, SECTOR_DEG, MAX_TRAVEL_H = 500.0, 40.0, 30.0
MIN_DETECTIONS, MIN_FRP_MW = 10, 200.0
MIN_WIND_KMH = 8.0
COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def enabled() -> bool:
    return bool(os.environ.get("FIRMS_MAP_KEY"))


def firms_url() -> str:
    w, s, e, n = BBOX
    return FIRMS_URL.format(key=os.environ["FIRMS_MAP_KEY"], w=w, s=s, e=e, n=n)


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
    if not detections or wind is None or wind[0] < MIN_WIND_KMH:
        return None
    speed, from_deg = wind
    hit = []
    for d in detections:
        km, bearing = distance_bearing(d["lat"], d["lon"])
        if km <= MAX_KM and _angle_diff(bearing, from_deg) <= SECTOR_DEG and km / speed <= MAX_TRAVEL_H:
            hit.append((km, bearing, d.get("frp") or 0.0))
    frp = sum(h[2] for h in hit)
    if len(hit) < MIN_DETECTIONS and frp < MIN_FRP_MW:
        return None
    nearest = min(hit, key=lambda h: h[0])
    return {"count": len(hit), "frp_mw": round(frp), "nearest_km": round(nearest[0]), "direction": COMPASS[int(((from_deg + 22.5) % 360) // 45)],
            "arrival_h": round(nearest[0] / speed, 1), "wind_kmh": round(speed), "wind_from_deg": round(from_deg)}


async def fetch_detections(client) -> list[dict]:
    r = await client.get(firms_url(), timeout=25)
    r.raise_for_status()
    return parse_firms_csv(r.text)


async def fetch_wind(client, hours: int = 24) -> tuple[float, float] | None:
    r = await client.get(WIND_URL.format(lat=LAT0, lon=LON0), timeout=15)
    r.raise_for_status()
    h = r.json()["hourly"]
    now = datetime.now().strftime("%Y-%m-%dT%H:00")
    i = next((k for k, t in enumerate(h["time"]) if t >= now), 0)
    return mean_wind(h["wind_speed_850hPa"][i : i + hours], h["wind_direction_850hPa"][i : i + hours])
