# Heat map: what was wrong, what replaced it

## What was wrong (found 18 Oct 2026)
The "Heat" layer drew one raster: the median Landsat 8/9 surface temperature of June-September 2026 scenes (`data/lst.tif`).

* It was **not live and had no forecast**: the hour slider never touched it. On a 10 °C autumn day it showed 28-48 °C with "hottest spots" pulsing red, next to a forecast chart that said 8-16 °C.
* It measured the **wrong quantity**: ground temperature of a clear morning (~10:30), not the air people feel. Scenes under 15 % cloud also bias the composite toward the hottest, clearest days.
* **Elevation read as heat**: across the bounding box LST correlates -0.76 with terrain height (only -0.03 on the flat valley floor). The "coolest spots" were all Vitosha forest, and the "city median" reference (35.7 °C) included the mountain, so every street looked warm.
* The colours were always **relative** (10th-90th percentile of the snapshot): a heatwave and a cold day looked the same.
* The key test: against 220 citizen temperature sensors (held out), the detrended Landsat anomaly ranks places **no better than chance** (Spearman 0.00-0.12 per hour).

## What replaced it
`T(place, hour) = city forecast + offset(place, weather of the hour)`, per street, per forecast hour, 48 hours ahead (`backend/ml/heat_field.py`, `backend/cai/heat.py`, `GET /api/cai/areas?metric=heat&hour=`).

* **Target**: a sensor's reading minus the median of all sensors in that hour (BME280 / DHT22 / SHT3x at the PM sensors, `data/history/env.csv`, Nov 2024 - Oct 2026, 220 sensors, 572,000 sensor-hours). Sensor bias common to all units and the city forecast error cancel; only the spatial part is learned. Sensor type is a nuisance term in training (an unsheltered BMP280 reads 1-2 degrees warm) and is dropped when predicting.
* **Features** (three; everything else was tested and dropped): terrain height, height above the ~3 km surroundings (cold air pools in the valley at night), GHSL building height within 1 km (urban heat island, the strongest by far). Tree canopy and NDVI added nothing; Landsat LST added nothing.
* **Weather terms** each feature acts through: sunshine, night, calm clear night, sunshine x calm. Ridge regression, so 220 places cannot be memorised (a boosted-tree version could, and ranked worse).
* **Heat bands** are the advice bands (feels-like 27 / 32 / 38 °C). Colour scales: *Temperature* (absolute, centred on 18 °C) and *Differences* (relative to the city value of the hour).

## Validation (5-fold over SENSORS; the held-out sensor's history is never seen)
Mean Spearman rank correlation between predicted and measured offsets of the sensors of one hour (`data/heat_field_eval.json`):

| Regime | New model | Old Landsat idea |
|---|---|---|
| all hours | 0.32 | 0.01 |
| summer nights | 0.42 | 0.00 |
| clear calm winter nights | 0.38 | 0.02 |
| winter days | 0.27 | 0.01 |
| summer sunny afternoons | 0.16 | 0.08 |

The spatial spread among sensors in one hour is ~3 °C (much of it sensor mounting); the model removes 3-10 % of the RMS (2.94 -> 2.85 °C overall, 2.28 -> 2.07 on summer nights). **Read it as**: the map gets the pattern (dense centre warmer on calm nights, mountain cooler, valley floor coldest at night) but not the exact street. Typical difference between the warmest and coolest areas is 1-4 °C.

## Limits and open items
* Sunny afternoons are the weakest regime: sensors in direct sun read high and the signal is noisy. Surface temperature (radiant exposure) can matter for a person walking in the sun, but that was not what the layer claimed, and it is not validated.
* **The street score's summer heat term (`backend/cai/features.py` H(e), gated by feels-like) still uses Landsat LST.** Switching it to this offset is the consistent next step; it changes routing, so it was left alone.
* No temperature reference station (the EEA stations do not report it), so the only truth is the citizen sensors.
* Refit: `PYTHONPATH=. .venv/bin/python -m backend.ml.heat_field` (needs `data/history/env.csv` from `python -m backend.data_prep.history_env`; also `make heat-field`).
