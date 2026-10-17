"""Hand-built winter inversion scenario (48 h) so the demo shows the interesting case."""
import math
from datetime import datetime, timedelta


def winter_inversion_hourly(day0: datetime) -> dict:
    """Return an Open-Meteo-shaped `hourly` dict starting at day0 00:00 (naive local time)."""
    times, blh, wind, t2m, t925, wind925, cloud, pm25, pm10, aqi, app, rad, rh = ([] for _ in range(13))
    pm = 38.0
    for i in range(48):
        t = day0 + timedelta(hours=i)
        h = t.hour
        # 1 around 01:00, 0 around 13:00
        night = 0.5 + 0.5 * math.cos((h - 1) / 24 * 2 * math.pi)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        blh.append(round(520 - 440 * night + (20 if i >= 24 else 0), 0))  # 80 m at night, ~520 m by day
        wind.append(round(6.5 - 5.1 * night, 1))  # 1.4 km/h at night
        t2m.append(round(-1.0 - 5.0 * night + (1.0 if i >= 24 else 0), 1))
        t925.append(round(t2m[-1] + 2.0 + 5.0 * night, 1))  # warm lid aloft
        wind925.append(round(6.0 - 2.0 * night, 1))
        cloud.append(10 if night > 0.5 else 25)
        app.append(round(t2m[-1] - 3.0 + 1.5 * (1 - night), 1))  # damp cold
        rad.append(round(max(0.0, 180 * math.sin(math.pi * (h - 7) / 10)) if 7 <= h <= 17 else 0.0, 0))
        rh.append(int(80 + 12 * night))
        # PM accumulates while mixing is shallow, relaxes a little by day
        pm += (5.0 * night - 1.2 * (1 - night)) * (1.0 if i < 30 else 0.6)
        pm = max(25.0, pm)
        pm25.append(round(pm, 1))
        pm10.append(round(pm * 1.35 + 6, 1))
        aqi.append(int(min(250, 40 + pm * 1.1)))
    return {
        "time": times,
        "boundary_layer_height": blh,
        "wind_speed_10m": wind,
        "temperature_2m": t2m,
        "temperature_925hPa": t925,
        "wind_speed_925hPa": wind925,
        "cloud_cover": cloud,
        "pm2_5": pm25,
        "pm10": pm10,
        "european_aqi": aqi,
        "apparent_temperature": app,
        "shortwave_radiation": rad,
        "relative_humidity_2m": rh,
    }


# ---------------------------------------------------------------------------------------------
# Summer heatwave and birch season: the other two things the For-me tab reacts to.
# Both keep the air well mixed (good stagnation score), so the story is heat / pollen, not an inversion.
# ---------------------------------------------------------------------------------------------

_POLLEN = ("alder", "birch", "grass", "mugwort", "olive", "ragweed")


def _daylight(h: int, rise: int, set_: int) -> float:
    """0..1 sine over the daylight hours."""
    return math.sin(math.pi * (h - rise) / (set_ - rise)) if rise <= h <= set_ else 0.0


def _pack(day0: datetime, point) -> dict:
    """point(i, t, sun) -> dict of one hour's values; returns an Open-Meteo-shaped hourly dict."""
    out: dict[str, list] = {}
    for i in range(48):
        t = day0 + timedelta(hours=i)
        row = point(i, t)
        row["time"] = t.strftime("%Y-%m-%dT%H:%M")
        for k, v in row.items():
            out.setdefault(k, []).append(v)
    return out


def summer_heatwave_hourly(day0: datetime) -> dict:
    """Late-July heatwave: 34 C then 36 C, feels like 37 to 39 C, nights that stay above 20 C. Dry grass pollen, no inversion."""

    def point(i, t):
        h, day2 = t.hour, i >= 24
        sun = _daylight(h, 5, 20)
        # skewed so the maximum lands mid-afternoon (~15:00), not at solar noon
        peak = math.sin(math.pi * ((h - 7) / 14) ** 1.2) if 7 <= h <= 21 else 0.0
        t2m = 22.0 + (12.0 + (2.0 if day2 else 0.0)) * peak
        night = 1.0 - sun
        pm = 14.0 + 6.0 * night + (2.0 if day2 else 0.0)
        return {
            "boundary_layer_height": round(650 + 1300 * sun),
            "wind_speed_10m": round(7.0 + 5.0 * sun, 1),
            "temperature_2m": round(t2m, 1),
            "temperature_925hPa": round(t2m - 3.0 - 6.0 * sun, 1),  # well mixed: no lid, even at night
            "wind_speed_925hPa": 10.0,
            "cloud_cover": 5,
            "apparent_temperature": round(t2m + 2.5 * sun + (0.5 if day2 else 0.0), 1),
            "shortwave_radiation": round(900 * sun),
            "relative_humidity_2m": round(55 - 25 * sun),
            "pm2_5": round(pm, 1),
            "pm10": round(pm * 1.6 + 6, 1),
            "european_aqi": int(35 + pm),
            **{f"{k}_pollen": (round(4.0 * math.sin(math.pi * (h - 6) / 8), 1) if k == "grass" and 6 <= h <= 14 else 0.0) for k in _POLLEN},
        }

    return _pack(day0, point)


def birch_season_hourly(day0: datetime) -> dict:
    """Mid-April, dry sunny and breezy: birch pollen high from late morning to evening, alder moderate. Cool nights, clear air."""

    def point(i, t):
        h, day2 = t.hour, i >= 24
        sun = _daylight(h, 6, 19)
        t2m = 6.0 + 13.0 * _daylight(h, 6, 22) + (1.0 if day2 else 0.0) if 6 <= h <= 22 else 6.0
        # pollen follows the warm, dry, windy part of the day and drops to a trickle overnight
        curve = 0.05 + 0.95 * max(0.0, math.sin(math.pi * (h - 8) / 11)) if 8 <= h <= 19 else 0.05
        k = 0.85 if day2 else 1.0
        pm = 11.0 - 3.0 * sun
        return {
            "boundary_layer_height": round(700 + 900 * sun),
            "wind_speed_10m": round(8.0 + 8.0 * sun, 1),
            "temperature_2m": round(t2m, 1),
            "temperature_925hPa": round(t2m - 3.0 - 3.0 * sun, 1),
            "wind_speed_925hPa": 14.0,
            "cloud_cover": 15,
            "apparent_temperature": round(t2m - 1.0, 1),
            "shortwave_radiation": round(650 * sun),
            "relative_humidity_2m": round(70 - 30 * sun),
            "pm2_5": round(pm, 1),
            "pm10": round(pm * 1.5 + 4, 1),
            "european_aqi": int(25 + pm),
            "birch_pollen": round(190 * k * curve, 1),
            "alder_pollen": round(28 * k * curve, 1),
            "grass_pollen": round(1.5 * k * curve, 1),
            "mugwort_pollen": 0.0, "olive_pollen": 0.0, "ragweed_pollen": 0.0,
        }

    return _pack(day0, point)


HOURLY = {"winter_inversion": winter_inversion_hourly, "summer_heatwave": summer_heatwave_hourly, "birch_season": birch_season_hourly}
SENSOR_SCALE = {"winter_inversion": (2.2, 28.0), "summer_heatwave": (1.2, 4.0), "birch_season": (0.9, 2.0)}


# Live sensor readings are re-scaled in the scenario so dots match the story.
def scenario_pm25(live_value: float, scenario: str = "winter_inversion") -> float:
    k, b = SENSOR_SCALE[scenario]
    return round(min(250.0, live_value * k + b), 1)
