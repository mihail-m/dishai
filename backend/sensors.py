"""Sensor.Community normalisation and cleaning."""
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
