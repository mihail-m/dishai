"""Pollen and allergy context: per-species forecast, typical season (from CAMS model history), UV / ozone / dust levels.

Everything is MODELLED (CAMS Europe via Open-Meteo, ~11 km), not counted by a trap: no open Bulgarian trap data exists. Species
bands live in advice.POLLEN_BANDS; the typical season comes from data/pollen_calendar.json (python -m backend.data_prep.pollen_calendar).
"""
import json
from datetime import date, timedelta
from pathlib import Path

from .advice import POLLEN_BANDS, POLLEN_LABEL, POLLEN_LEVELS, POLLEN_TYPES, _pollen_level

CALENDAR_PATH = Path(__file__).resolve().parent.parent / "data" / "pollen_calendar.json"

# kind: what the species is; foods: oral-allergy (pollen-food) cross-reactions reported in the allergy literature; "may", not "will"
SPECIES = {
    "alder": {"kind": "tree", "foods": ["apple", "pear", "cherry", "peach", "hazelnut", "almond", "carrot", "celery"]},
    "birch": {"kind": "tree", "foods": ["apple", "pear", "cherry", "peach", "hazelnut", "almond", "kiwi", "carrot", "celery"]},
    "grass": {"kind": "grass", "foods": ["tomato", "melon", "watermelon", "orange", "peanut"]},
    "mugwort": {"kind": "weed", "foods": ["celery", "carrot", "fennel", "spices", "sunflower seeds"]},
    "olive": {"kind": "tree", "foods": ["olive", "ash and privet pollen cross-react"]},
    "ragweed": {"kind": "weed", "foods": ["melon", "watermelon", "banana", "cucumber", "courgette"]},
}
assert set(SPECIES) == set(POLLEN_TYPES)

# --- non-pollen triggers (hourly peaks over a day) ---------------------------------------------------------------------
UV_BANDS, UV_LEVELS = (3.0, 6.0, 8.0, 11.0), ("low", "moderate", "high", "very high", "extreme")  # WHO UV index categories
OZONE_BANDS, OZONE_LEVELS = (100.0, 180.0), ("low", "elevated", "high")  # ug/m3: WHO 8 h peak-season guideline, EU hourly information threshold
DUST_BANDS, DUST_LEVELS = (50.0, 100.0), ("low", "notable", "heavy")  # ug/m3 of mineral (often Saharan) dust: hand-set, indicative


def _lvl(v: float | None, bands: tuple[float, ...]) -> int:
    return 0 if v is None else sum(v >= b for b in bands)


def _day(t: str) -> str:
    return t[:10]


def forecast(hours: list[dict], now_idx: int = 0, days: int = 4, horizon_h: int = 96) -> dict:
    """Per species: daily peaks and the hourly series from now. available=False when the feed has no pollen values at all."""
    by_day: dict[str, list[dict]] = {}
    for h in hours:
        by_day.setdefault(_day(h["time"]), []).append(h)
    seen = any(v is not None for h in hours for v in (h.get("pollen") or {}).values())
    if not seen:
        return {"available": False, "species": []}
    keys = sorted(by_day)[:days]
    out = []
    for k in POLLEN_TYPES:
        daily = []
        for d in keys:
            vals = [(h["time"], (h.get("pollen") or {}).get(k)) for h in by_day[d]]
            vals = [(t, v) for t, v in vals if v is not None]
            if not vals:
                daily.append({"date": d, "peak": None, "peak_hhmm": None, "mean": None, "level": "none"})
                continue
            t, peak = max(vals, key=lambda x: x[1])
            lv = _pollen_level(k, peak)
            daily.append({"date": d, "peak": round(peak, 1), "peak_hhmm": t[11:16], "mean": round(sum(v for _, v in vals) / len(vals), 1), "level": POLLEN_LEVELS[lv]})
        series = [(h["time"], (h.get("pollen") or {}).get(k)) for h in hours[now_idx : now_idx + horizon_h]]
        now = next((v for _, v in series if v is not None), None)
        out.append({
            "type": k, "label": POLLEN_LABEL[k], "kind": SPECIES[k]["kind"], "foods": SPECIES[k]["foods"],
            "bands": {"moderate": POLLEN_BANDS[k][0], "high": POLLEN_BANDS[k][1]},
            "now": None if now is None else {"grains": round(now, 1), "level": POLLEN_LEVELS[_pollen_level(k, now)]},
            "days": daily,
            "hourly": [{"time": t, "grains": None if v is None else round(v, 1)} for t, v in series],
        })
    return {"available": True, "species": out}


def environment(hours: list[dict], days: int = 4) -> dict:
    """UV index, ozone and mineral dust: daily peak per day plus the level. Each is available=False when the feed lacks it."""
    by_day: dict[str, list[dict]] = {}
    for h in hours:
        by_day.setdefault(_day(h["time"]), []).append(h)
    keys = sorted(by_day)[:days]
    spec = {"uv": ("uv", UV_BANDS, UV_LEVELS, ""), "ozone": ("ozone", OZONE_BANDS, OZONE_LEVELS, "µg/m³"), "dust": ("dust", DUST_BANDS, DUST_LEVELS, "µg/m³")}
    out = {}
    for name, (field, bands, levels, unit) in spec.items():
        rows = []
        for d in keys:
            vals = [(h["time"], h.get(field)) for h in by_day[d] if h.get(field) is not None]
            if not vals:
                rows.append({"date": d, "peak": None, "peak_hhmm": None, "level": None})
                continue
            t, peak = max(vals, key=lambda x: x[1])
            rows.append({"date": d, "peak": round(peak, 1), "peak_hhmm": t[11:16], "level": levels[_lvl(peak, bands)]})
        out[name] = {"available": any(r["peak"] is not None for r in rows), "unit": unit, "levels": list(levels), "bands": list(bands), "days": rows}
    return out


def environment_summary(hours: list[dict]) -> dict:
    """Peaks over the given hours (the next 24 h) for the advice cards: {uv|ozone|dust: {peak, level (index), peak_hhmm} or None}."""
    out = {}
    for name, field, bands in (("uv", "uv", UV_BANDS), ("ozone", "ozone", OZONE_BANDS), ("dust", "dust", DUST_BANDS)):
        vals = [(h["time"], h.get(field)) for h in hours if h.get(field) is not None]
        if not vals:
            out[name] = None
            continue
        t, peak = max(vals, key=lambda x: x[1])
        out[name] = {"peak": round(peak, 1), "level": _lvl(peak, bands), "peak_hhmm": t[11:16]}
    return out


# --- typical season -------------------------------------------------------------------------------------------------


def load_calendar(path: Path = CALENDAR_PATH) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _doy_label(doy: int) -> str:
    d = date(2025, 1, 1) + timedelta(days=int(doy) - 1)  # %-d is not portable to Windows, which the launcher supports
    return f"{d.day} {d.strftime('%b')}"


def season_status(cal: dict | None, key: str, today: date) -> dict:
    """Where `today` sits in the species' typical season: in_season | upcoming | over | none (no stable season in the history)."""
    s = ((cal or {}).get("species", {}).get(key) or {}).get("season")
    if not s:
        return {"type": key, "status": "none"}
    a, b = int(s["start_doy"]), int(s["end_doy"])
    doy = min(today.timetuple().tm_yday, 365)
    base = {"type": key, "start": _doy_label(a), "end": _doy_label(b), "start_doy": a, "end_doy": b}
    if a <= doy <= b:
        return {**base, "status": "in_season", "days_left": b - doy}
    if doy < a:
        return {**base, "status": "upcoming", "in_days": a - doy}
    return {**base, "status": "over", "in_days": a + 365 - doy}  # next year's start


def seasons(cal: dict | None, today: date) -> list[dict]:
    return [season_status(cal, k, today) for k in POLLEN_TYPES]

