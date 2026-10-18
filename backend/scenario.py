"""Hand-built winter inversion scenario (48 h) so the demo shows the interesting case."""
import math
from datetime import datetime, timedelta


def winter_inversion_hourly(day0: datetime) -> dict:
    """Return an Open-Meteo-shaped `hourly` dict starting at day0 00:00 (naive local time)."""
    times, blh, wind, t2m, t925, wind925, cloud, pm25, pm10, aqi, app, rad, rh = ([] for _ in range(13))
    pm = 22.0
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
        pm += (2.6 * night - 1.2 * (1 - night)) * (1.0 if i < 30 else 0.6)
        pm = max(16.0, pm)
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


def serbia_wildfire_hourly(day0: datetime) -> dict:
    """Mid-August, hot and dry, with large fires burning in eastern Serbia. A steady north-westerly flow brings the smoke over Sofia during the
    first day and it stays for the second: PM2.5 climbs from about 14 to the high 90s, the sun is dimmed, optical depth is very high."""

    def point(i, t):
        h = t.hour
        sun = _daylight(h, 5, 20)
        peak = math.sin(math.pi * ((h - 7) / 14) ** 1.2) if 7 <= h <= 21 else 0.0
        ramp = min(1.0, max(0.0, (i - 8) / 14))                      # the smoke front arrives between about 08:00 and 22:00 on day one
        ease = 1.0 - 0.45 * min(1.0, max(0.0, (i - 40) / 8))         # and begins to thin a little late on day two
        s = ramp * ease
        t2m = 21.0 + 11.0 * peak - 2.0 * s * peak                    # the smoke shades the afternoon a little
        pm = 14.0 + 85.0 * s
        return {
            "boundary_layer_height": round((500 + 1200 * sun) * (1.0 - 0.3 * s)),
            "wind_speed_10m": round(8.0 + 4.0 * sun, 1),
            "temperature_2m": round(t2m, 1),
            "temperature_925hPa": round(t2m - 3.0 - 5.0 * sun, 1),
            "wind_speed_925hPa": 24.0,                               # a steady flow from the west-north-west carries the plume
            "cloud_cover": 10,
            "apparent_temperature": round(t2m + 2.0 * sun, 1),
            "shortwave_radiation": round(800 * sun * (1.0 - 0.35 * s)),
            "relative_humidity_2m": round(55 - 25 * sun),
            "pm2_5": round(pm, 1),
            "pm10": round(pm * 1.25 + 10.0, 1),
            "european_aqi": int(min(250, 35 + pm * 1.2)),
            "uv_index": round(8.5 * sun * (1.0 - 0.4 * s), 1), "ozone": round(70 + 60 * sun, 1), "dust": 8.0, "aerosol_optical_depth": round(0.15 + 1.0 * s, 2),
            **{f"{k}_pollen": (round(1.0 * math.sin(math.pi * (h - 6) / 8), 1) if k == "grass" and 6 <= h <= 14 else 0.0) for k in _POLLEN},
        }

    return _pack(day0, point)


# Fire detections the demo pretends NASA FIRMS reported: a cluster around Knjazevac and Zajecar in eastern Serbia, about 130 km north-west of Sofia, and the
# 850 hPa wind that carries the smoke towards the city. Placed on a fixed pseudo-random pattern; the numbers are demo values, not observations.
def _wildfire_detections() -> list[dict]:
    out = []
    for k in range(84):
        a, b = math.sin(k * 12.9898) * 43758.5453, math.sin(k * 78.233) * 24634.6345
        ra, rb = a - math.floor(a), b - math.floor(b)
        out.append({"lat": round(43.52 + (ra - 0.5) * 0.36, 4), "lon": round(22.22 + (rb - 0.5) * 0.46, 4), "frp": round(14 + 70 * ra * rb, 1), "conf": "n"})
    return out


WILDFIRE_DETECTIONS = _wildfire_detections()
WILDFIRE_WIND = (26.0, 305.0)  # km/h, the direction it blows FROM (north-west)

# The date each demo pretends to be, so the season outlook matches the story: (month, day)
SCENARIO_DAY = {"winter_inversion": (1, 15), "summer_heatwave": (7, 25), "birch_season": (4, 15), "serbia_wildfire": (8, 12)}
HOURLY = {"winter_inversion": winter_inversion_hourly, "summer_heatwave": summer_heatwave_hourly, "birch_season": birch_season_hourly, "serbia_wildfire": serbia_wildfire_hourly}
# Applied to RAW sensor readings (before calibration to the reference scale, which roughly doubles them): winter turns the live
# autumn readings into an episode, the other two keep clean air.
SENSOR_SCALE = {"winter_inversion": (2.0, 14.0), "summer_heatwave": (0.8, 0.5), "birch_season": (0.9, 1.0), "serbia_wildfire": (2.0, 22.0)}


# The winter demo also gets a geography, so the maps and the route planner have something to show. It follows what Sofia's winter episodes look like:
# stove-heated and traffic-choked districts on the flat ground collect the smoke, while parks and the foot of Vitosha, above the inversion lid or
# fed by cleaner air, stay much cleaner. Each entry is (name, lat, lon, radius km, change): a Gaussian bump multiplying the sensor readings.
# Places are approximate and the numbers are DEMO values, not measurements.
_DEMO_POOLS = {
    "winter_inversion": (
        # --- polluted: dense, stove-heated or busy districts ---
        ("Nadezhda", 42.736, 23.310, 1.7, +0.56),
        ("Orlandovtsi and Obelya", 42.728, 23.262, 1.5, +0.45),
        ("Fakulteta and Krasna polyana", 42.686, 23.287, 1.2, +0.59),
        ("Poduyane and Iztok", 42.685, 23.363, 1.6, +0.52),
        ("Slatina", 42.690, 23.382, 1.4, +0.45),
        ("Druzhba and Iskar", 42.650, 23.405, 1.7, +0.39),
        ("Lyulin", 42.718, 23.254, 1.8, +0.24),
        ("Mladost", 42.656, 23.383, 1.5, +0.21),
        ("the old centre and its boulevards", 42.699, 23.320, 1.2, +0.24),
        # --- clean: parks and the slopes of Vitosha ---
        ("Borisova gradina", 42.684, 23.344, 1.5, -0.82),
        ("South Park (Yuzhen park)", 42.674, 23.309, 1.4, -0.82),
        ("Lozenets", 42.665, 23.317, 1.5, -0.72),
        ("Studentski grad and the Mladost park belt", 42.650, 23.345, 1.5, -0.72),
        ("Boyana", 42.645, 23.264, 2.0, -0.93),
        ("Dragalevtsi and Simeonovo", 42.628, 23.310, 2.4, -0.94),
        ("Knyazhevo and the Lozen foothills", 42.655, 23.225, 2.0, -0.82),
        ("Gorna Banya", 42.718, 23.218, 2.0, -0.82),
        ("Vrana and the north-east park", 42.718, 23.395, 1.8, -0.80),
    ),
    # smoke comes in from the north-west: the west of the city sees it first and thickest, the south-east a little less
    "serbia_wildfire": (
        ("the west, first in the path of the smoke", 42.710, 23.200, 7.0, +0.30),
        ("the south-east", 42.650, 23.430, 6.0, -0.20),
    ),
}


def demo_geography(lat: float | None, lon: float | None, scenario: str) -> float:
    """Multiplier (about 0.05 to 2.1) for a sensor at lat/lon in the demo scenario; 1.0 elsewhere or without a position.
    Polluted places add up; of the clean ones the strongest wins, so neighbouring parks do not pile into zero."""
    pools = _DEMO_POOLS.get(scenario)
    if not pools or lat is None or lon is None:
        return 1.0
    dirty, clean = 0.0, 1.0
    for _name, plat, plon, rad, delta in pools:
        d = math.hypot((lat - plat) * 111.0, (lon - plon) * 111.0 * math.cos(math.radians(plat)))
        bump = delta * math.exp(-0.5 * (d / rad) ** 2)
        if bump > 0:
            dirty += bump
        else:
            clean = min(clean, 1.0 + bump)
    return max(0.05, (1.0 + dirty) * clean)


def has_geography(scenario: str | None) -> bool:
    return bool(scenario) and scenario in _DEMO_POOLS


def demo_geography_array(lat, lon, scenario: str):
    """demo_geography for arrays of positions (the street midpoints): the same rule, evaluated for the whole network at once."""
    import numpy as np
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    dirty, clean = np.zeros_like(lat), np.ones_like(lat)
    for _name, plat, plon, rad, delta in _DEMO_POOLS.get(scenario, ()):
        d = np.hypot((lat - plat) * 111.0, (lon - plon) * 111.0 * np.cos(np.radians(plat)))
        bump = delta * np.exp(-0.5 * (d / rad) ** 2)
        if delta > 0:
            dirty += bump
        else:
            clean = np.minimum(clean, 1.0 + bump)
    return np.maximum(0.05, (1.0 + dirty) * clean).astype("float32")


# Live sensor readings are re-scaled in the scenario so dots match the story.
def scenario_pm25(live_value: float, scenario: str = "winter_inversion", lat: float | None = None, lon: float | None = None) -> float:
    k, b = SENSOR_SCALE[scenario]
    return round(min(250.0, (live_value * k + b) * demo_geography(lat, lon, scenario)), 1)
