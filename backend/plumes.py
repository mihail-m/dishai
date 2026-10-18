"""Dust, smoke and other regional aerosol events over Sofia, read straight from the Copernicus CAMS forecast.

CAMS (Copernicus Atmosphere Monitoring Service) already models desert dust and the emissions of fires; we do not second-guess it. This module only
fetches the hourly CAMS Europe fields (via Open-Meteo, no key: PM10, PM2.5, mineral dust, aerosol optical depth, carbon monoxide), compares them with
what is usual for the month (data/cams_climate.json, baked by `make cams-climate` from CAMS's own history), and says what kind of event it is,
because the right response differs: desert dust is coarse (close windows facing the wind, wet-clean); smoke is fine (purifier, FFP2 mask, no
burning); a winter episode of heating and traffic in trapped air is neither, and the answer is to emit less and ventilate when the air is deepest.

Rules (docs/ALERTS.md lists them with what was and was not checked):
  dust            CAMS surface dust >= 20 ug/m3 in the next 36 h, or dust >= 35 % of PM10 while PM10 >= 25
  smoke           fires near or upwind (backend/fires.py) AND CAMS carbon monoxide or PM2.5 above its own 90th percentile for the month
  smoke_possible  fires near or upwind, CAMS does not show it (yet)
  local           heating season (Oct-Apr), trapped air (stagnation score >= 35) and PM2.5 or CO above the month's 90th percentile, no dust or smoke
  worse day       a forecast day whose CAMS daily-mean PM2.5 or PM10 is above the month's 75th percentile AND at the WHO daily guideline (PM2.5 15, PM10 45)
Not in the keyless feed: the vertical profile of a plume and CAMS's fire-emission fields (GFAS), which need a Copernicus ADS account. Alerts use the
surface value (lowest model level); the column optical depth is shown next to it so a plume that is mostly overhead is visible as such.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .cai.graph import DATA

LAT, LON = 42.6977, 23.3219
CAMS_URL = ("https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={LAT}&longitude={LON}&hourly=pm10,pm2_5,dust,aerosol_optical_depth,carbon_monoxide&past_days=2&forecast_days=4&timezone=Europe%2FSofia")
KEYS = ("pm10", "pm2_5", "dust", "aerosol_optical_depth", "carbon_monoxide")
CLIMATE_PATH = DATA / "cams_climate.json"
DUST_WATCH = 20.0  # ug/m3 at the surface; same value as the dust alert (backend/alerts.py)
DUST_SHARE, DUST_SHARE_MIN_PM10 = 0.35, 25.0
HEATING_MONTHS = (10, 11, 12, 1, 2, 3, 4)
WINDOW_H = 36
AOD_HIGH = 0.4
WHO_PM25_DAY, WHO_PM10_DAY = 15.0, 45.0  # ug/m3, WHO 2021 24-hour guidelines (PM10 45 is also the EU limit from 2030; today's EU limit is 50)
MIN_DAY_HOURS = 18

_climate_cache: dict = {"mtime": None, "data": {}}


def climate(path: Path = CLIMATE_PATH) -> dict:
    """{month number as str: yardstick} from data/cams_climate.json; empty when the file has not been baked (the comparisons are then simply absent)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _climate_cache["mtime"] != mtime:
        try:
            _climate_cache.update(mtime=mtime, data=json.loads(path.read_text(encoding="utf-8")).get("months", {}))
        except (OSError, ValueError):
            _climate_cache.update(mtime=mtime, data={})
    return _climate_cache["data"]


def rows_from(data: dict) -> list[dict]:
    """Open-Meteo air-quality JSON -> [{time, pm10, pm2_5, dust, aerosol_optical_depth, carbon_monoxide}] (None where missing)."""
    h = data["hourly"]
    return [{"time": t, **{k: (h.get(k) or [None] * len(h["time"]))[i] for k in KEYS}} for i, t in enumerate(h["time"])]


def _window(rows: list[dict], now: str, hours: int = WINDOW_H) -> list[dict]:
    start = now[:13] + ":00"
    return [r for r in rows if r["time"] >= start][:hours]


def _mean(vals) -> float | None:
    v = [x for x in vals if x is not None]
    return float(np.mean(v)) if v else None


def _max3(vals: list[float | None]) -> float | None:
    """Highest 3-hour mean (a single odd hour is not an event)."""
    best = None
    for i in range(len(vals) - 2):
        w = vals[i : i + 3]
        if None in w:
            continue
        m = sum(w) / 3
        best = m if best is None or m > best else best
    return best


def outlook(rows: list[dict], today: str, clim: dict | None = None, days: int = 4) -> list[dict]:
    """One entry per forecast day with at least MIN_DAY_HOURS hours of CAMS: the daily means, the dust peak, and how the day compares with the month."""
    clim = climate() if clim is None else clim
    by: dict[str, list[dict]] = {}
    for r in rows:
        if r["time"][:10] >= today:
            by.setdefault(r["time"][:10], []).append(r)
    out = []
    for d in sorted(by)[:days]:
        rs = by[d]
        if sum(r["pm10"] is not None and r["pm2_5"] is not None for r in rs) < MIN_DAY_HOURS:
            continue
        pm10, pm25, dust = _mean(r["pm10"] for r in rs), _mean(r["pm2_5"] for r in rs), _mean(r["dust"] for r in rs)
        dmax = max(rs, key=lambda r: r["dust"] if r["dust"] is not None else -1)
        c = clim.get(str(int(d[5:7])), {})
        by_pm25 = c.get("pm25_day_p75") is not None and pm25 >= c["pm25_day_p75"] and pm25 >= WHO_PM25_DAY
        by_pm10 = c.get("pm10_day_p75") is not None and pm10 >= c["pm10_day_p75"] and pm10 >= WHO_PM10_DAY
        out.append({"date": d, "pm10": round(pm10, 1), "pm25": round(pm25, 1), "usual_pm10": c.get("pm10_day_p50"), "usual_pm25": c.get("pm25_day_p50"),
                    "worse_than_usual": bool(by_pm25 or by_pm10), "worse_by": "pm25" if by_pm25 else "pm10" if by_pm10 else None,
                    "dust_mean": round(dust or 0.0, 1), "dust_max": round(dmax["dust"] or 0.0, 1), "dust_peak_time": dmax["time"] if (dmax["dust"] or 0) > 0 else None,
                    "dust_share": round((dust or 0.0) / pm10, 2) if pm10 else None})
    return out


def classify(rows: list[dict], now: str, threat: dict | None, stagnant: bool, clim: dict | None = None) -> dict:
    """What the aerosol in the next WINDOW_H hours probably is, with the evidence. Never claims more than the rules above."""
    clim = climate() if clim is None else clim
    win = _window(rows, now)
    month = int(now[5:7])
    c = clim.get(str(month), {})
    pm10, dust = _mean(r["pm10"] for r in win), _mean(r["dust"] for r in win)
    dust_peak = max((r["dust"] or 0.0 for r in win), default=0.0)
    share = (dust / pm10) if (dust is not None and pm10) else None
    co3, pm253 = _max3([r["carbon_monoxide"] for r in win]), _max3([r["pm2_5"] for r in win])
    co_hi = bool(co3 is not None and c.get("co_hour_p90") and co3 >= c["co_hour_p90"])
    pm25_hi = bool(pm253 is not None and c.get("pm25_hour_p90") and pm253 >= c["pm25_hour_p90"])
    aod_max = max((r["aerosol_optical_depth"] for r in win if r["aerosol_optical_depth"] is not None), default=None)

    evidence, parts = [], []
    if dust_peak >= DUST_WATCH or (share is not None and share >= DUST_SHARE and (pm10 or 0) >= DUST_SHARE_MIN_PM10):
        parts.append("dust")
        evidence.append(f"CAMS mineral dust up to {dust_peak:.0f} µg/m³ at the surface" + (f", {share:.0%} of PM10" if share is not None else ""))
    if threat:
        near = "near" if threat.get("near") else "upwind"
        if co_hi or pm25_hi:
            parts.append("smoke")
            what = " and ".join(n for n, f in (("carbon monoxide", co_hi), ("PM2.5", pm25_hi)) if f)
            evidence.append(f"{threat['count']} fire detections {near} and CAMS {what} unusually high for {datetime(2000, month, 1).strftime('%B')}")
        else:
            parts.append("smoke_possible")
            evidence.append(f"{threat['count']} fire detections {near}, but CAMS does not show a smoke plume in the next {WINDOW_H} h")
    if not parts and month in HEATING_MONTHS and stagnant and (co_hi or pm25_hi):
        parts.append("local")
        evidence.append("heating season, trapped air and CAMS " + " and ".join(n for n, f in (("carbon monoxide", co_hi), ("PM2.5", pm25_hi)) if f) + " above the usual for the month: heating and traffic, not a plume")
    kinds = [p for p in parts if p in ("dust", "smoke")]
    kind = "mixed" if len(kinds) == 2 else (parts[0] if parts else "none")
    label = {"dust": "Desert dust: coarse particles", "smoke": "Smoke from fires: fine particles", "smoke_possible": "Fires nearby, no smoke in the forecast yet",
             "local": "Local winter pollution: heating and traffic in trapped air", "mixed": "Dust and smoke together", "none": "No regional plume"}[kind]
    vertical = None
    if aod_max is not None and aod_max >= AOD_HIGH and dust_peak < 10:
        vertical = f"Optical depth is high ({aod_max:.2f}) but surface dust is low ({dust_peak:.0f} µg/m³): more aerosol overhead than at street level. Alerts follow the surface value."
    elif dust_peak >= DUST_WATCH:
        vertical = f"The forecast brings the dust to the surface (up to {dust_peak:.0f} µg/m³ in the lowest model level)."
    return {"kind": kind, "components": parts, "label": label, "evidence": evidence, "dust_share": None if share is None else round(share, 2), "dust_peak": round(dust_peak, 1),
            "pm25_peak_3h": None if pm253 is None else round(pm253, 1), "co_elevated": co_hi, "pm25_elevated": pm25_hi, "aod_max": None if aod_max is None else round(aod_max, 2),
            "vertical": vertical, "window_hours": len(win)}


def chart_hours(rows: list[dict], now: str, back: int = 12, ahead: int = 60) -> list[dict]:
    """Hourly CAMS series for the plume chart: a little of the past and the coming days."""
    start = (datetime.strptime(now[:13], "%Y-%m-%dT%H") - timedelta(hours=back)).strftime("%Y-%m-%dT%H:00")
    return [{"time": r["time"], "pm10": r["pm10"], "pm2_5": r["pm2_5"], "dust": r["dust"]} for r in rows if r["time"] >= start][: back + ahead]


def assess(rows: list[dict], now: str, threat: dict | None, stagnant: bool) -> dict:
    clim = climate()
    return {"days": outlook(rows, now[:10], clim), "plume": classify(rows, now, threat, stagnant, clim), "hours": chart_hours(rows, now), "yardstick": bool(clim)}
