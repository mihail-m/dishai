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


# Live sensor readings are re-scaled in the scenario so dots match the story.
def scenario_pm25(live_value: float) -> float:
    return round(min(250.0, live_value * 2.2 + 28.0), 1)
