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
        "uv_index": [round(1.2 * max(0.0, math.sin(math.pi * (t.hour - 8) / 8)) if 8 <= t.hour <= 16 else 0.0, 1) for t in (day0 + timedelta(hours=i) for i in range(48))],
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
        pm = 8.0 + 4.0 * night + (1.0 if day2 else 0.0)  # a clean mixed summer day; the story here is heat, not particulates
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
            # strong sun, an afternoon ozone build-up, and a Saharan dust plume arriving on the second day
            "uv_index": round(10.5 * sun, 1), "ozone": round(55 + 105 * sun * (1.1 if day2 else 1.0), 1),
            "dust": round(18.0 + (70.0 * sun if day2 else 0.0), 1), "aerosol_optical_depth": round(0.15 + (0.35 if day2 else 0.0), 2),
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
            "uv_index": round(5.5 * sun, 1), "ozone": round(60 + 35 * sun, 1), "dust": 12.0, "aerosol_optical_depth": 0.12,
            "birch_pollen": round(190 * k * curve, 1),
            "alder_pollen": round(28 * k * curve, 1),
            "grass_pollen": round(1.5 * k * curve, 1),
            "mugwort_pollen": 0.0, "olive_pollen": 0.0, "ragweed_pollen": 0.0,
        }

    return _pack(day0, point)


# The date each demo pretends to be, so the season outlook matches the story: (month, day)
SCENARIO_DAY = {"winter_inversion": (1, 15), "summer_heatwave": (7, 25), "birch_season": (4, 15)}
HOURLY = {"winter_inversion": winter_inversion_hourly, "summer_heatwave": summer_heatwave_hourly, "birch_season": birch_season_hourly}
# Applied to RAW sensor readings (before calibration to the reference scale, which roughly doubles them): winter turns the live
# autumn readings into an episode, the other two keep clean air.
SENSOR_SCALE = {"winter_inversion": (2.2, 28.0), "summer_heatwave": (0.8, 0.5), "birch_season": (0.9, 1.0)}


# The winter demo also gets a geography, so the maps and the route planner have something to show. Real inversion episodes pool cold, stove-heated
# air in the low eastern and central districts and leave the slopes of Vitosha and the north cleaner. Each entry is (lat, lon, radius km, change):
# a Gaussian bump multiplying the sensor readings. These are DEMO values, not measurements.
_DEMO_POOLS = {
    "winter_inversion": (
        (42.690, 23.345, 1.8, +1.10),   # the smoke pool over Iztok, Poduyane and the centre-east
        (42.700, 23.310, 1.4, +0.60),   # the old centre, boulevards in still air
        (42.665, 23.395, 2.0, +0.40),   # stove districts of the south-east (Mladost side, Druzhba)
        (42.665, 23.335, 2.0, -0.50),   # Lozenets and the Hladilnika slope: a cleaner way south of the centre
        (42.640, 23.290, 2.6, -0.35),   # Vitosha foot: Boyana, Dragalevtsi, Simeonovo
        (42.735, 23.330, 2.4, -0.30),   # the north: Gorna Banya side
        (42.690, 23.270, 1.8, -0.25),   # the green corridor of the west (Borisova gradina side)
    ),
}


def demo_geography(lat: float | None, lon: float | None, scenario: str) -> float:
    """Multiplier (about 0.6 to 1.8) for a sensor at lat/lon in the demo scenario; 1.0 elsewhere or without a position."""
    pools = _DEMO_POOLS.get(scenario)
    if not pools or lat is None or lon is None:
        return 1.0
    f = 1.0
    for plat, plon, rad, delta in pools:
        d = math.hypot((lat - plat) * 111.0, (lon - plon) * 111.0 * math.cos(math.radians(plat)))
        f += delta * math.exp(-0.5 * (d / rad) ** 2)
    return max(0.4, f)


# Live sensor readings are re-scaled in the scenario so dots match the story.
def scenario_pm25(live_value: float, scenario: str = "winter_inversion", lat: float | None = None, lon: float | None = None) -> float:
    k, b = SENSOR_SCALE[scenario]
    return round(min(250.0, (live_value * k + b) * demo_geography(lat, lon, scenario)), 1)
