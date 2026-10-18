"""Sensor.Community normalisation and cleaning."""
import math
import statistics
from datetime import datetime, timezone

MAX_PM25 = 500.0
MAX_AGE_MIN = 30


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalise(raw: list[dict]) -> list[dict]:
    """Drop indoor sensors and records without a valid P2; keep the latest record per sensor id."""
    latest: dict[int, dict] = {}
    for rec in raw:
        loc = rec.get("location") or {}
        if str(loc.get("indoor", 0)) == "1":
            continue
        values = {v.get("value_type"): _float(v.get("value")) for v in rec.get("sensordatavalues", [])}
        pm25 = values.get("P2")
        if pm25 is None or pm25 < 0 or pm25 > MAX_PM25:
            continue
        lat, lon = _float(loc.get("latitude")), _float(loc.get("longitude"))
        if lat is None or lon is None:
            continue
        sid = (rec.get("sensor") or {}).get("id", rec.get("id"))
        out = {
            "id": sid,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "pm25": round(pm25, 1),
            "pm10": values.get("P1"),
            "timestamp": rec.get("timestamp"),
        }
        prev = latest.get(sid)
        if prev is None or (out["timestamp"] or "") >= (prev["timestamp"] or ""):
            latest[sid] = out
    return list(latest.values())


def fresh_only(records: list[dict], now: datetime | None = None, max_age_min: int = MAX_AGE_MIN) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out = []
    for r in records:
        try:
            ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if (now - ts).total_seconds() <= max_age_min * 60:
            out.append(r)
    return out


# A single citizen sensor can read many times its neighbours (a fault, a spider web in the inlet, a fire next to it). One such reading would be
# spread over a whole district by the spatial model, so it is dropped when it stands far above sensors nearby that agree with each other.
OUTLIER_RADIUS_KM = 5.0
OUTLIER_MIN_NEIGHBOURS = 2
OUTLIER_RATIO = 4.0  # at least this many times the neighbours' median ...
OUTLIER_MARGIN = 15.0  # ... and at least this many ug/m3 above it (raw sensor scale), so quiet air never trips the ratio alone


def drop_isolated_outliers(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split raw (uncalibrated) records into (kept, dropped). Only high readings are tested, and only where at least
    OUTLIER_MIN_NEIGHBOURS other sensors are within OUTLIER_RADIUS_KM: with fewer there is nothing to compare against, so the reading stays."""
    kept, dropped = [], []
    for r in records:
        kx = 111.32 * math.cos(math.radians(r["lat"]))
        near = [o["pm25"] for o in records if o is not r and math.hypot((o["lat"] - r["lat"]) * 110.57, (o["lon"] - r["lon"]) * kx) <= OUTLIER_RADIUS_KM]
        if len(near) >= OUTLIER_MIN_NEIGHBOURS:
            med = statistics.median(near)
            if r["pm25"] >= max(OUTLIER_RATIO * med, med + OUTLIER_MARGIN):
                dropped.append(r)
                continue
        kept.append(r)
    return kept, dropped
