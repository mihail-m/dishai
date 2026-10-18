# Data and testing in brief

What data the PM2.5 model has, what was tested with it, and what came out. Details and tables: [PM_MODEL.md](PM_MODEL.md).

## Bottom line

- **City level: reliable.** A calibrated, probabilistic city-wide PM2.5 forecast beats "the city median stays" from about 6 h ahead and stays within ±5 µg/m³ on forecast episodes.
- **Place level: only neighbourhood scale is supported.** Sensors say how a neighbourhood differs from the city, not how one street differs from the next. A 250 m cell map ranks hidden sensors as well as the street map.
- **Why: sensor offsets.** Each citizen sensor has a stable bias (about ×1.6) that is not a property of its place, unrelated to its neighbours' bias in every city tested, and that fades over years. It accounts for roughly 85 % of how sensors rank against each other, and no input we found predicts it.
- **More data does not fix that**: more hours, more sensors, an older era, local weather, buildings, other cities. Each gives a few percent at most.

## Data we have

| Data | What | Coverage | Used for |
|---|---|---|---|
| Sensor.Community Sofia (PM) | ~260 outdoor SDS011 sensors, hourly, via the public archive | Nov 2024 – Oct 2026, 191 sampled days (one week in seven plus the 14 worst CAMS days), 702k sensor-hours | training and testing everything |
| Sensor.Community Sofia 2017–19 (Kaggle zip) | 487 sensors, PM and BME280 | Jul 2017 – Jul 2019, every day, 4.5 M sensor-hours | offset/structure replication, pooled-era test |
| Sensor-side temperature, humidity, pressure | BME280 / DHT22 beside 203 of 258 PM sensors | the 130 history days, 575k sensor-hours | local-input tests |
| PM10 history | same sensors, PM10 channel | 191 days, 697k sensor-hours | PM10 calibration, construction-dust test |
| Weather and CAMS | Open-Meteo forecast (one point) and CAMS PM2.5/PM10; a 35-point grid over Sofia | Aug 2024 – Oct 2026, 672k grid hours | the level model; local-weather test |
| EEA reference, Sofia | PM2.5 at Hipodruma (BG0050A); BG0070A excluded (flat, level step); PM10 at 5 stations | 2026 only (EEA serves nothing earlier through its API) | calibration and validation |
| EEA reference, Europe | 52 PM2.5 stations in 29 cities with ≥ 4 sensors within 1.5 km, plus their sensors | 2026, 96k station-hours, 1,477 hours above 50 µg/m³ | multi-city calibration |
| Other cities' sensor networks | Berlin, Cologne, Stuttgart, Budapest, Plovdiv, Skopje | 2026, same 87 sample days | cross-city test of the local model |
| Place layers | Copernicus DEM, tree inventory, Sentinel-2 NDVI, Landsat surface heat, OSM construction sites and buildings, GHSL building height/volume/population | static | street-trend and place-feature tests |
| Traffic, NO2 (from the parallel session) | BGTOLL ring-road counts, EEA NO2, Google/TomTom live traffic | 2026 | NO2 card; traffic tested as a PM input |

**Offered but not usable:** `air-quality-dataset_1.ipynb` (500-row table, no location or time), `air-quality-dataset_2.zip` (unlabelled gas-sensor columns), `air-quality-dataset_3.zip` (UCI Air Quality, one Italian device, 2004), Hugging Face `neuralsorcerer/air-quality` (fully synthetic Kolkata) and `PM25Vision` (street photos). **Not available to us:** reference data for 2017–19, census heating-fuel data, the district-heating area, low-emission-zone geometry.

## How we test

Never random rows: whole weeks are held out, and for place-level models whole sensors too ("bi-fold": a test fold is sensors the model never trained on in weeks it never saw). Every comparison is on the same rows against simple baselines (persistence, CAMS × bias, kriging). Thresholds are chosen on training data only. Negative results are kept.

## What was tested

| Question | Test | Result |
|---|---|---|
| Are the three PM numbers (banner, chart, map) consistent? | one model behind all screens | Yes; the stagnation score is context, not the alert |
| Do citizen sensors read true? | calibration against EEA Hipodruma, weeks held out | They read ~half of the reference, less so in humid air. Calibration cuts error ~40 %; fitted on 52 stations in 29 other cities it is within 3 % of the Sofia one |
| Does the calibration hold at high PM? | reliability by calibrated value across cities | Calibrated > 50 µg/m³ reads 5 % low on average (786 hours) |
| Does the city level forecast well? | quantile model, week-blocked | Intervals right in cross-validation, too narrow on the latest months |
| Does the alert beat the old rule? | back-test, winter 2025/26 | Red tier: 12 % false alarms but misses a third of episode windows. Amber tier matches the old rule's recall at 55 % false alarms instead of 71 % |
| Can sensors map streets? | leave-one-sensor-out, kriging vs IDW | Better than IDW everywhere; but a 250 m cell map ranks as well as the street map (0.347 vs 0.349) |
| Do street features help? | traffic, canopy, NDVI, surface heat, construction, OSM low-rise housing, GHSL | None generalises to unseen sensors; the traffic term was removed as an assumption |
| Is construction dust visible? | pre-specified PM10 check, 191 days | No: interval includes zero |
| Do local inputs (weather grid, nearby temperature, buildings) help? | ML on top of kriging, untouched folds | No |
| Does a model with coordinates and nearest readings do better? | direct model, bi-fold | No; it loses to kriging, and coordinates hardly matter |
| Is it too little data? | learning curve, older era, 6 other cities | Gains of 0–5 %; more sensors help slowly, more hours stop at ~400 |
| Is Sofia unrepresentative? | same tests in 6 other cities | No; offsets behave the same everywhere and Sofia's neighbours are the most informative of the seven |
| Why the ceiling? | oracle that knows each sensor's offset; offset correlation by distance and over time | Knowing the offset halves the error and lifts ranking from 0.3 to 0.8. Offsets are unrelated between neighbours and fade over years |
| Is NO2 a PM input? | NO2 added to the level model | No |

## What would change the picture

Data that separates the instrument from the place: sensors run side by side with each other or a reference station, or sensor metadata (inlet height, enclosure). Then each sensor could be calibrated individually and the place's own signal, which the local weather, building and vegetation layers are meant to explain, would be readable. Heating-fuel and district-heating data would address the main winter source directly.
