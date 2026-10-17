# The learned PM2.5 model

The Clean Air Index (CAI) used to rest on a hand-weighted formula for PM2.5. It now rests on a model learned from citizen-sensor history. Everything downstream (CAI, heat term, routes, area view) is unchanged.

## What it replaces

```
before   PM = B + R + 0.5 · T · rush · A · (1 − 0.3·G)       all weights assumed
now      PM = level(hour) · exp(street(e)) + R               weights learned from data
```

`R` is still the live-sensor correction (nearest sensors, inverse-distance weighting, shrunk to 0 far from any sensor). If `data/pm_model.joblib` is missing, the app falls back to the old formula (`C.USE_ML = False` forces it).

## The two parts

**1. City level: gradient-boosted trees** (scikit-learn `HistGradientBoostingRegressor`). Predicts the city-wide PM2.5 for an hour as a **ratio to the CAMS forecast**: `level = CAMS × exp(f)`. A ratio matters because trees cannot predict beyond the values they were trained on, whereas a ratio scales when CAMS says 100.

| Inputs | What they are |
|---|---|
| CAMS PM2.5 and PM10 | the regional forecast for that hour |
| Weather | temperature, humidity, boundary layer height, wind at 10 m and 925 hPa, cloud, radiation, inversion strength (925 hPa minus ground), the stagnation score |
| Time | hour of day, weekend |
| Live nowcast | median of the live sensors now, CAMS now, their ratio, and the lead (hours ahead) |

What the model leaned on most (permutation importance): CAMS itself (0.31), the sensor/CAMS ratio now (0.22), humidity (0.10), the sensor median now (0.08), temperature (0.06). Everything else is small.

**2. Street difference: a tiny linear term.** How much dirtier or cleaner than the city a street is:

```
street(e) = (a + a_s·S/100) · z(log traffic T) + (b + b_s·S/100) · z(terrain height)
```

Fitted values (log-units per standard deviation): traffic +0.015, +0.013 more at full stagnation; terrain height −0.040, −0.031 more at full stagnation. In plain terms: in a full inversion a street one SD busier is about 3% dirtier and one SD lower in the valley about 7% dirtier. The effect is capped at ×2 / ×0.5.

Why so small: 66% of the street-to-street variation in the sensors is a fixed per-sensor offset (calibration, siting). Only traffic and terrain height predicted it on sensors the model had not seen. Tree canopy, NDVI, surface heat and richer models memorised individual sensors and did not generalise, so they are **not** in the model.

## Training data

- 261 outdoor Sensor.Community PM sensors in Sofia, hourly means, 702k sensor-hours over 191 days between Nov 2024 and Oct 2026: a random one-week-in-seven sample plus the 14 worst-CAMS episodes of the last two winters.
- Open-Meteo historical forecast (weather) and CAMS history, in the same variables the live app uses.
- Each training row gets a random forecast lead of 0–47 h, so the nowcast inputs mean the same thing in training and in serving.

Rebuild: `make dem history construction train` (history takes about an hour and is resumable).

## How to test it

`python -m backend.ml.train` prints all of this; the results are stored in the model file and shown by `/api/validation` (`ml` block).

1. **Hidden sensors.** 5-fold cross-validation where a fifth of the sensors is never seen in training (and training weeks differ from test weeks). Question: does it work at a spot with no sensor?
2. **Hidden sensors and future days.** Train on the first 75% of days and 75% of sensors, test on the rest.
3. **Baselines on the same rows:** the old formula, CAMS × sensor bias, raw CAMS, and "the city median now stays as it is" (persistence).
4. **Slices that matter:** by lead time (0–5 h, 6–23 h, 24–47 h), and **episode hours** (CAMS forecasts 50 µg/m³ or more).
5. **Street skill measured separately:** rank correlation between sensors within one hour, using only the street part of the prediction.
6. **Live check:** `/api/validation` runs leave-one-out on today's sensor snapshot.
7. **Unit tests** (`backend/tests/test_ml.py`): feature layout, missing inputs, direction of the traffic and terrain effects, the cap, stale model files ignored, end-to-end `CaiModel`, and fallback. Plus the 100 existing tests.

## Results (latest run)

| | Learned model | Old formula | Persistence |
|---|---|---|---|
| Average error, hidden sensors + future days | **2.6 µg/m³** | 4.3 | 2.8 |
| Average error, hidden sensors, all weeks | **5.1** | 8.6 | 5.6 |
| Error at 24–47 h lead (future days) | **2.9** | 4.5 | 3.2 |
| **Episode hours (CAMS ≥ 50): error / bias** | **12.7 / −3.5** | 27.7 / **+22.8** | 13.5 / −4.9 |
| Street ranking within an hour (hidden sensors) | **0.16** | 0.14 | – |

- The big win is the **level**, especially in episodes: when CAMS forecasts an inversion, the old formula overshot the sensors by about 23 µg/m³ on average. The learned model stays within a few µg/m³ of the sensors on average.
- At 0–5 h ahead it is no better than persistence (expected: the live sensors already give the level). The gain appears from about 6 h out.
- The **street part is weak**: 0.12–0.16 rank correlation. It is real and slightly better than the old formula, but it cannot say that one street is reliably cleaner than the next. On today's clean-air snapshot neither model beats "everything is the median" (median error 0.94 vs 0.90).

## Things I tried that did not help (so they are not in the model)

| Idea | Result on sensors the model never saw |
|---|---|
| CAMS NO₂, SO₂, CO, O₃ as inputs (from the Bagging-GBDT paper's pollutant list) | no gain: average error 5.13 vs 5.10 without, episode-hour error 12.9 vs 12.7 |
| Tree canopy, NDVI, surface heat, richer street models | memorised individual sensors, no generalisation |
| **Buildings under construction** (OSM, 1,881 areas, about 5 km²) | no effect on PM2.5 under any cut, see "Construction sites" below. Shown as context in "Why is this red?", not in the score. |
| Live-sensor PM10/PM2.5 ratio (dust vs smoke) | **not tested**: needs the PM10 history re-download, paused. The downloader already keeps PM10 for when it is re-run. |


## Construction sites: what we know and how to improve it

**Data.** OSM closed areas tagged `landuse=construction` (1,007) or `building=construction` (873). Median footprint 450 m² (buildings) and 1,700 m² (land). Dating is poor: 39 have a start or opening date, 236 a `check_date` since 2024, so most could be finished or abandoned. No public Sofia permit register was found (see sources below); GIS-Sofia says it supplies cadastral and construction-plan data on request.

**Result** (`python -m backend.ml.construction_check`). No cut shows an effect on PM2.5 at the 261 sensors. Rank correlation with a sensor's persistent offset stays between −0.03 and +0.07 for radius 50–500 m, big or small sites, buildings or land, recently checked sites. It is also flat on workday daytime, dry and windy hours (within-hour +0.03 to +0.05), where dust should be strongest. A within-sensor design (workday vs weekend, same hours, sensor as its own control) gives −1.6% (95% interval −8.5% to +5.9%) near sites. The test could detect about ±0.12 in rank correlation, and traffic and terrain (0.13–0.14) were detectable, so any effect on PM2.5 is smaller than those.

**Why that is plausible.** Construction dust is mostly coarse. One site study estimated 207 kg/yr PM10 against 59.5 kg/yr PM2.5 and modelled hourly means of 112 vs 11 µg/m³ (Čavić et al. 2025); a London study found PM10 limit exceedances next to sites (Azarmi et al. 2016). PM10 is where a signal would show, and it is not in the history yet (only 9 days: PM10/PM2.5 1.81 near sites vs 1.71 elsewhere, and 1.79 vs 1.65 at night, so not specific to working hours).

**What was tried to improve it**
1. **PM10 instead of PM2.5: not run yet.** The Sensor.Community archive stopped answering from this machine after a fast download (it works for others; the live API is fine). `backend/data_prep/history.py` now runs slowly, writes to its own file (`--name sensors_pm10`), and stops instead of recording empty days. A waiter script restarts it when the archive answers. Then rerun `python -m backend.ml.construction_check` (PM10 pre-specified check below). Only 9 days of PM10 exist today.
2. **Sentinel-2 activity per site** (`make construction-activity`, 10 m bands inside each footprint, this summer vs last): 1,389 of 1,881 sites scored, 877 bare now, 65 cleared since last year. Sensors near **bare-now** sites: +0.07 to +0.08 rank correlation (interval includes 0). Sensors near **cleared-since** sites: +0.11 to +0.12, but only 9 to 30 sensors, the interval barely excludes 0 at one radius, and the "green now" control scores +0.12 on the same partial measure. Treat as a lead, not a result.
3. **More OSM tags:** only 2 of 1,881 sites were last checked before 2023, so dropping stale ones changes nothing; weighting by building height (24 sensors near tall sites) gives +0.04.
4. **Dated permits: skipped.** A public register exists ([nag.sofia.bg](https://nag.sofia.bg/RegisterBuildingPermitsPortal/Index)) but it has no export, no coordinates, and a copyright notice requiring written consent, so it needs an agreement with the municipality.
5. **Advice note, shipped:** asthma and kids/older-relative users in the busiest third of districts (and at least 10 mapped sites) get a "Many building sites in your district" card. It says dust is mostly coarse and not in the PM2.5 map, and that OSM sites are undated. District shares come from `public/data/construction_districts.json` (`make construction-districts`; Studentski 6.1% and Lozenets 4.1% are highest).

**The one check that would settle it (pre-specified, so it is not another look at many cuts):** on PM10, for sites flagged cleared-since at 300 m, compare workday vs weekend at 9-17 per sensor against sensors with no site within 300 m. If the interval excludes zero on PM10, add a site-activity term to the PM10-aware model.

Sources: [Azarmi et al. 2016, London](https://pubs.rsc.org/en/content/articlehtml/2016/em/c5em00549c) · [Čavić et al. 2025, Serbia](https://acta.bibl.u-szeged.hu/88696/1/proceedings_of_isaep_2025_282-285.pdf) · [Petit et al. 2022, Wallonia (Sentinel-1/2)](https://doaj.org/article/f74a70981b184ab9a7614c9c79528519) · [GIS-Sofia data provision](https://gis-sofia.bg/en/services/cadastre-and-spatial-planning/provision-of-data) · [OSM start_date](https://wiki.openstreetmap.org/wiki/Start_date)

## Limits to keep in mind

- **Target is what citizen sensors read.** They drift and read high in humid air. The model predicts that, not true PM2.5.
- **Few extreme episodes.** The history peaks at a city median of 55 µg/m³. A 100+ µg/m³ event is extrapolation: the model scales with the forecast (inputs ×5 gives about ×3.8), but it has never been checked against such a day.
- **Street signal is weak.** Treat street-to-street differences as indicative and the city level as the reliable part.
- **Nowcast leak.** The city median used as an input includes the sensor being tested; with about 250 sensors the effect is negligible.

## Suggested acceptance bar before presenting

- Episode-hour bias within ±5 µg/m³ and average error below the old formula's. **Met** (−3.5, 12.7 vs 27.7).
- Beats persistence at 6 h or more. **Met.**
- Street ranking above the old formula on hidden sensors. **Met, narrowly** (0.16 vs 0.14).
- Re-run `make train` monthly during the heating season and watch the episode-hour bias.


---

# Unified model: one calibrated PM2.5 behind every screen

Branch `unified-pm-model`. The sections above describe the model as it was; the phases below change it. Each phase ends with a measured result. A negative result is reported, not tuned away.

## Phase 0: one number for the banner, the chart and the map

Before, three screens showed three PM2.5 numbers: the banner fired on the stagnation score, the chart plotted raw CAMS, the map used the learned model. Now the hourly **city level comes from the learned model** (`backend/alert.py`, the same `PmModel.level_pm` call the street map uses), and:

- **Banner** is red when that level stays at or above 25 µg/m³ (EU 24 h limit) for 3 hours in a row, starting within 24 h or already running. Amber when any hour reaches 15 (WHO 24 h guideline). The stagnation score is supporting text ("the air is trapped, score 72") and a chart overlay, no longer the trigger. A trapped-air forecast with clean PM2.5 is now a green banner that says so (`test_clean_forecast_with_a_high_stagnation_score_is_not_a_red_banner`).
- **Message** carries the expected value: "Tomorrow night 22:00–06:00: PM2.5 around 38 µg/m³. Sensitive groups should limit time outside." "Everyone" from 50 µg/m³.
- **Chart** plots the model level; raw CAMS is a faint dashed, labelled line.
- **Advice and dashboard** use the same alert object. District rows share the city level for now; per-district levels come with the spatial field (Phases 2–3). The district-map shading is still the score-times-elevation index.
- Thresholds are in `backend/cai/config.py` (`PM_WATCH`, `PM_WARNING`, `PM_EVERYONE`, `ALERT_MIN_HOURS`).
- Fallbacks, flagged in the alert's `basis`: no model file → CAMS × live-sensor bias (`cams_bias`); no PM series at all → the old stagnation rule (`stagnation`).
- **Merge decision:** Phase 0 shipped on the branch together with Phase 1 (below), so the banner never showed uncalibrated numbers as final. The alert has a `scale` field (`sensor` or `reference`); the banner footnote says "citizen-sensor scale" whenever the model is not calibrated.

## Phase 1: reference data and calibration

### Data
`make reference` (`backend/data_prep/reference_eea.py`) downloads hourly PM2.5 and PM10 from the EEA download API for the Sofia stations; coordinates and station type come from the EEA station metadata table, never guessed. Licence: EEA content is CC BY 4.0 ([legal notice](https://www.eea.europa.eu/en/legal-notice)); attribution: "European Environment Agency (EEA) e-reporting, reported by Bulgaria". `data/history/` stays git-ignored.

What the API actually gives for Sofia:

| | |
|---|---|
| PM2.5 stations | **BG0050A Hipodruma** (urban background) and BG0070A (rural-nearcity). That is all. BG0045A and BG0075A are not in Sofia. |
| PM10 stations | BG0040A Nadezhda, BG0050A, BG0052A Druzhba, BG0073A Pavlovo (traffic), BG0079A Mladost (traffic) |
| Coverage | dataset 1 (up-to-date stream): **2026-01-01 onwards only**. Dataset 2 (verified): empty for Bulgaria. Dataset 3 (AirBase): ends years before the sensor history. |
| Time stamp | `Start` is UTC+1 all year; subtracting 1 h is right (correlation with the sensors peaks at lag 0 after the shift: r 0.75 vs 0.72 / 0.72 at ±1 h) |

**BG0070A is excluded.** It has no daily cycle (12–13 µg/m³ at every hour), its level steps from about 16 to about 8 in June, and it correlates 0.25 with Hipodruma while the five PM10 stations correlate 0.63–0.79 with each other. No sensor is within 2 km of it to check it against.

### What this means: the calibration rests on one station
Only Hipodruma can anchor PM2.5. **13 sensors are within 1.5 km (7 within 1 km, 31 within 2 km, none within 500 m)**, giving 1,991 overlapping hours in 41 sampled weeks of 2026. Only 3 of those hours exceed 50 µg/m³ and 88 exceed 25. The calibration says nothing reliable about heavy episodes; it extrapolates.

The README's claim that the municipal stations "report no PM2.5" is true of the WAQI feed only.

### Method
Unit: the hourly **median** of the sensors within R of the station against the station's hourly value (a median is what the city level uses, and it keeps each sensor's private offset out of the global curve; that offset belongs to Phase 2). 253 of the 254 sensors with a known type are SDS011, so "sensor type" cannot be tested and is not a feature. Evaluation: whole weeks held out (GroupKFold over weeks), never random rows.

| Candidate | MAE µg/m³ | bias | r |
|---|---|---|---|
| raw sensors, no calibration | 5.29 | −4.98 | 0.750 |
| a. ratio only | 4.05 | −0.16 | 0.747 |
| b. log-linear + RH | 3.21 | −0.48 | 0.804 |
| c. b + temperature | 3.24 | −0.47 | 0.800 |
| **d. c + heating-season flag** | **3.16** | −0.45 | 0.802 |
| e. gradient boosting (upper bound) | 3.36 | −0.94 | 0.777 |

d is the best and the simplest within one standard error of the best by block-wise MAE (b is within 0.05 µg/m³ of it, so the temperature and season terms are worth little). The GBM is no better than the linear model: the inputs hold no more than this. Fitted: `log(1+ref) = 0.79 + 0.92·log(1+raw) − 0.63·RH + 0.15·T/10 + 0.31·heating`. A raw reading of 10 maps to about 20 at 35 % RH, 17 at 65 % and 14 at 90 % (winter or summer within 1 µg/m³). The factor falls as humidity rises: sensors under-read least when it is humid. That is the same physical direction as the known humidity over-read of optical sensors (particles swell), but around a baseline where Sofia's sensors read low. The calibrated median's own error is `sigma_log` = 0.37 (log units), which the spatial stage adds to each sensor's nugget.

Disc radius barely matters (MAE 3.12 / 3.16 / 3.14 for 1 / 1.5 / 2 km). A last-quarter-of-the-year fit gives MAE 3.96, bias −0.95 (future check).

### PM10 cross-check
The PM10 sensor history is only the 9 autumn days of 2026 the archive gave before it stopped answering (a re-pull stalled; `make history --name sensors_pm10 --since 2026-01-01` is ready for when it answers). Against the five PM10 stations, 984 station-hours, RH only 26–82 %:

- Same fit form, **RH coefficient −0.632 for PM10 against −0.609 for PM2.5**. Independent stations, a different pollutant, the same humidity behaviour.
- Calibrated PM10 MAE 7.2 vs raw 15.3 µg/m³; median ratio per RH bin 0.91–1.01 up to 80 % RH, 1.35 above (n=70).
- This PM10 fit is saved as a stop-gap for the live WAQI comparison; it is indicative, fitted on 9 days.

### Acceptance (proposed bars)
| Bar | Result |
|---|---|
| Median calibrated/reference ratio 0.85–1.15 in every RH bin, held-out weeks | **Met.** Sensors near the station: 0.97–1.01. Median of **all** calibrated sensors in the city: 0.93–1.02 (n per bin 214–636). |
| MAE ≥ 40 % lower than raw | **Near-miss at city scale.** Near the station 40.3 % (3.16 vs 5.29). City median of all sensors: **39.3 %** (3.30 vs 5.44). Not loosened. |
| Report the >25 and >50 bins with counts | >25 reference: 88 hours, median ratio 0.80, bias −6.4. **>50: 3 hours**, ratio 0.4. |

The ratio in the high reference bins looks low partly by construction (binning on the outcome shows regression to the mean). Binning on the **calibrated** value instead: when the calibrated median is 25–50, the reference is 0.97 of it (n=66); 10–25: 0.99 (n=681); 0–10: 1.04 (n=1,244). So the calibration is not biased given what a user sees, but it cannot reach the highest values: it never predicted above 50.

### Live WAQI check (PM10, one hour, 2026-10-17 around 21:30 local)
Ratio of citizen to reference PM10 at five stations (6–19 sensors within 1.5 km each): raw **0.46×** (0.35–0.64), calibrated **1.31×** (0.99–1.60). The raw error was −0.78 in log units, the calibrated error +0.27, but it now overshoots at three of five stations. WAQI's PM10 is converted from AQI, the PM10 calibration was fitted on 9 days, and this is one night hour, so read it as "closer, not yet right".

### Retrained level model (calibrated targets)
`make calibrate train`. The model file now records that it was trained on calibrated values and which calibration; it refuses to load if the calibration file is missing or has changed (otherwise its input, the live city median, would be off by about 2×). Numbers are now in reference-scale µg/m³ (old rows were on the sensor scale: not comparable).

| | Learned model | Old formula | CAMS × bias | Persistence |
|---|---|---|---|---|
| Error, hidden sensors + future days (MAE) | **4.67** | 5.67 | 5.29 | 4.86 |
| Error, hidden sensors, all weeks | **7.24** | 10.28 | 9.84 | 7.69 |
| Episode hours (CAMS ≥ 50): MAE / bias | **15.98 / −3.73** | 32.17 / +24.27 | 31.27 / +22.62 | 17.17 / −5.84 |
| Street ranking within an hour | **0.16** (0.118 on the future split) | 0.12 | 0.00 | – |

The learned model keeps its advantage on episode hours and still sits within ±5 µg/m³ of bias there. Within-hour street ranking is unchanged in kind (about 0.12–0.16). Calibration did not make the street part better, as expected: it scales values, it does not add spatial information.

### Visible effect
**The map and the sensor dots are about 2× more polluted than before on the same day** (median sensor ×1.87 across the history). This is a correction, not a regression: the sensors read about half of the reference station.

## Phase 2: per-sensor offsets and kriging instead of IDW

`make spatial` (`backend/ml/spatial_fit.py`) fits it; `backend/cai/krige.py` serves it; `C.SPATIAL_METHOD = "idw"` brings the old method back (it is also the fallback if `data/spatial.joblib` is missing or was made against another calibration).

### The model
In log1p space, with the city level and the traffic/terrain trend taken out:

```
residual e0[s,t] = log1p(sensor) - log1p(city median) - trend(s,t)
offset_s         = mean over the sensor's hours of e0, shrunk toward 0 (empirical Bayes)
kriged residual  = simple kriging of (e0 - offset) from the 24 nearest sensors, exponential covariance, per-sensor noise
PM(street, t)    = expm1( log1p(trend) + rho(lead) * kriged residual )
```
The kriging weights (streets × sensors, sparse) and variances are computed once per sensor snapshot; each forecast hour is a sparse matrix-vector product.

**What an offset means.** A sensor's persistent deviation is a mix of calibration error and real local pollution (a stove next door, a courtyard), and without co-location the two cannot be separated. The rule: **remove it when the sensor is used to estimate other places** (otherwise one over-reading unit paints its neighbours), and **at the sensor's own location report both values** (`/api/sensors`: `pm25` and `pm25_adjusted`, `offset_pct`). A sensor with no history gets offset 0 and the full offset variance as extra noise, so it counts for less.

### What the data say
| | |
|---|---|
| Offsets | SD **0.48** in log1p units (×1.6), lag-1 autocorrelation of the hourly residual 0.86. Stable: first vs second half of the weeks, **r = 0.85** on 247 sensors. Hundreds of effectively independent hours per sensor, so the shrinkage factor is ≈1 for nearly all (249 of 252 of today's live sensors have an offset). |
| Variogram (exponential, all hours) | nugget 0.072, partial sill 0.150, **range a = 12.6 km** (practical range about 38 km) |
| by season | heating: 0.082 / 0.174 / 8.8 km · summer: 0.066 / 0.122 / 17.4 km · polluted heating hours (city > 15): 0.106 / 0.182 / 6.7 km |
| Empirical semivariance | below 300 m about 0.06 · 0.6–1.3 km about 0.09 · 4 km 0.11 · 7 km 0.14 · 14 km 0.17 |
| Persistence of the kriged residual | slope on the residual h hours later: 0.89 (0 h) · 0.76 (1) · 0.63 (2) · 0.54 (3) · 0.33 (6) · 0.18 (12) · 0.29 (24) · 0.12 (36) · 0.19 (47). Fitted `rho(h) = 0.19 + 0.71·exp(-h/4 h)`. |

**Reading this: the field carries almost no street-scale information.** The semivariance climbs from 0.06 below 300 m to 0.09 at about 1 km, then keeps climbing slowly to 0.17 at 14 km: only about a sixth of the spatial variance is at scales under a kilometre, the rest is neighbourhood-to-district scale (valley floor and edge, east-west contrasts the traffic and terrain trend does not capture). The unstructured part (nugget, 32 % of the fitted sill) cannot be told apart from measurement noise. So sensors can say how a *neighbourhood* differs from the city, not how one street differs from the next. This is what Phase 5 turns into the decision.

The old IDW kept a snapshot residual at full strength for the whole 48 h window. The data say it is mostly gone after 6 h (slope 0.33) and about a fifth remains afterwards.

### Evaluation: leave-one-sensor-out over historical hours
Whole weeks held out (5 folds); offsets, variogram and the interval scale are refitted on the other weeks; at each held-out hour every sensor is hidden in turn and predicted from the others at that hour. The city level is taken as known, which isolates the spatial stage. 2,680 hours, 602k predictions; half the hours are polluted heating-season hours so that this regime is well covered. Median absolute error in µg/m³ / mean within-hour Spearman:

| Held-out hours | level only | level + trend | IDW (old) | kriging, offsets kept | **kriging, offsets removed** |
|---|---|---|---|---|---|
| all (2,680 h) | 3.02 / – | 2.93 / 0.176 | 3.07 / 0.225 | 2.85 / 0.200 | **2.77 / 0.251** |
| heating season (1,645 h) | 5.24 / – | 5.09 / 0.191 | 5.05 / 0.266 | 4.71 / 0.259 | **4.54 / 0.315** |
| summer (1,035 h) | 1.52 / – | 1.44 / 0.153 | 1.59 / 0.159 | 1.48 / 0.105 | 1.50 / 0.149 |
| polluted heating, city > 15 (930 h) | 9.06 / – | 8.81 / 0.218 | 8.57 / 0.290 | 8.05 / 0.299 | **7.78 / 0.341** |
| city > 25 (436 h) | 12.94 / – | 12.68 / 0.221 | 12.20 / 0.300 | 11.41 / 0.334 | **11.04 / 0.360** |

- Removing the neighbours' offsets is what makes kriging work: kept, it is only marginally better than the trend.
- **In summer kriging adds nothing over the trend alone** (1.50 vs 1.44 µg/m³; ranking 0.149 vs IDW's 0.159). The field is weak at clean levels, where sensor noise (±2 µg/m³) is as large as the signal.
- Caveats: the four trend coefficients were fitted on all sensors (in-sample for them, identical for every method); the neighbour count was set to 24 after looking at the cross-validated numbers for 6/12/24/48 (gain from 12 to 24 is 0.07 µg/m³ and 0.01 in ranking; 48 is the same as 24), so that comparison is slightly optimistic. The hidden sensor's own offset (×1.6 SD) is part of what it is scored against and is unpredictable; this puts a ceiling on every correlation here.

**Uncertainty.** Per-street SD (log1p units) = scale × √(nugget + offset variance + kriging variance), shrinking the explained part with the lead through ρ². The raw SD gave 80 % intervals that held 0.83 (heating) and 0.88 (summer) of the hidden readings; a per-season factor fitted on the training weeks (heating 0.88, summer 0.63; the summer noise is smaller at clean levels) brings them to **0.796 (heating), 0.798 (summer)**, 0.777 in polluted heating hours and 0.772 above 25 µg/m³. The level part of the uncertainty is added in Phase 3.

### Acceptance
| Bar | Result |
|---|---|
| Leave-one-sensor-out median error below IDW over historical hours | **Met** in every cut (all 2.77 vs 3.07; heating 4.54 vs 5.05; summer 1.50 vs 1.59; polluted heating 7.78 vs 8.57) |
| Within-hour Spearman not lower than IDW | **Met** overall (0.251 vs 0.225), heating (0.315 vs 0.266), polluted heating (0.341 vs 0.290). **Not met in summer: 0.149 vs 0.159** |
| 80 % intervals cover 75–85 %, heating and summer separately | **Met** (0.796 and 0.798) after the per-season scale; before it, summer was 0.88 |

Live snapshot (`/api/validation`, one clean autumn evening, 259 sensors): kriging ρ 0.146 / median error 2.39, IDW 0.083 / 2.39, trend only 0.138 / 2.20. Nothing beats the trend on a clean day, in line with the summer rows.

### Cost
Once per sensor snapshot (every few minutes at most): kriging weights for 119k street segments take about 1.4 s (model init was 0.08 s). The 48 forecast hours take 1.0 s (was 1.3 s). End to end, a window build goes from about 1.4 s to 2.4 s.

## Phase 3: probabilistic city level and alert

### What changed
- The city level is now three gradient-boosted **quantile** models (p10, p50, p90) of the log ratio to CAMS. The p50 replaces the old squared-error model as the central value everywhere (slightly better: MAE 7.22 vs 7.24 hidden sensors/all weeks; 4.65 vs 4.67 future days). `PmModel` format 4.
- The raw quantile fits are over-confident out of sample (about 19 % of readings fell below the p10 instead of 10 %). They are widened by **split-conformal** corrections per lead bucket (0–5, 6–23, 24–47 h) × level class (p50 below / above 15 µg/m³), taken from week-blocked out-of-fold residuals.
- The banner shows a **probability**: P(city level ≥ 25 µg/m³ for ≥ 3 hours in a row, somewhere in the next 24 h). Hourly values are two-piece normals through the p10/p50/p90; the standardised errors of consecutive hours follow an AR(1) process with φ = 0.88 (measured: lag-1 correlation 0.89, lag-2 0.77, lag-3 0.67, lag-6 0.50, lag-12 0.34; AR(1) gives 0.88 / 0.78 / 0.68 / 0.47 / 0.22). Common random numbers make P monotone in the level (unit-tested).
- Banner: **red from P ≥ 0.7** (`p_red`), **amber ("possible episode") from P ≥ 0.2** (`p_watch`), the message carries the chance: "Tomorrow night 22:00–06:00: PM2.5 around 38 µg/m³ (85 % chance). Sensitive groups should limit time outside." The chart draws the 10th–90th percentile band. Both values and φ live in the model file (`meta.alert`), config has the same values as fallbacks.
- **The red threshold p\*=0.7 is the output of the stated rule** (maximum Peirce skill on the training period, heating season 2024/25). **The amber threshold 0.2 is a policy choice, not fitted** ("a 1-in-5 chance deserves a heads-up"); I set it before the final back-test but after an earlier back-test run had shown the sweep, so it is not untouched by the test period.

### Quantile coverage (target 0.10 below p10, 0.10 above p90)
Week-blocked 5-fold (every fold refits the quantile models and the widening on the other weeks) and the future split (first 75 % of days / 75 % of sensors, scored on the rest):

| Slice | 5-fold: below p10 / above p90 | Future days: below p10 / above p90 |
|---|---|---|
| all | 0.085 / 0.080 | 0.039 / **0.163** |
| heating season | 0.072 / 0.096 | 0.017 / 0.221 (7.7k rows) |
| summer | 0.103 / 0.058 | 0.043 / 0.152 |
| lead 0–5 h · 6–23 h · 24–47 h | 0.080 / 0.082 · 0.087 / 0.081 · 0.090 / 0.077 | 0.053 / 0.094 · 0.033 / 0.188 · 0.023 / 0.253 |
| CAMS ≥ 50 hours | 0.101 / 0.032 | no such hours in the test split |
| observed city level ≥ 25 (selected on the outcome) | 0.007 / 0.174 | – |

Within ±5 points in the 5-fold evaluation. **Not within ±5 points on the future split**: readings in the last quarter of the history fall above the p90 16 % of the time (22 % in the heating-season rows, 25 % at 24–47 h): the intervals drift too narrow when the model is asked about later days than it was trained on. I did not tune that away. The last row is a selection effect (conditioning on a high outcome), not a defect, but it says the upper tail is where the model is least certain.

### Back-test: the new alert against the old rules
Windows = the 24 h after issue times 00/06/12/18 local with the live nowcast at issue time; observed episode = calibrated city median ≥ 25 for ≥ 3 h in a row; probabilities are out of fold (whole weeks held out). 336 windows (203 in heating season, 77 with an episode). p\* chosen on heating season 2024/25 (39 windows, only 5 without an episode: a thin base), evaluated on 2025/26 + October 2026 (164 windows, 43 with an episode). The history is one week in seven plus the 14 worst-CAMS days, so episodes are over-represented against the climate: the false-alarm ratios are for this sample.

| Test period (164 windows, 32 distinct episodes) | alerts | POD | false-alarm ratio | Peirce | episodes missed | median lead |
|---|---|---|---|---|---|---|
| **New: red, P ≥ 0.7** | 33 | 0.67 | **0.12** | **0.64** | 6 | 18 h |
| **New: amber tier, P ≥ 0.2** | 91 | **0.95** | **0.55** | 0.54 | 1 | 18 h |
| Phase 0 rule (median level ≥ 25 for 3 h) | 37 | 0.67 | 0.22 | 0.61 | 6 | 18 h |
| Old rule: stagnation score ≥ 60 in any hour | 141 | 0.95 | 0.71 | 0.13 | 1 | 18 h |
| Persistence (city already ≥ 25 at issue) | 21 | 0.49 | 0.00 | 0.49 | 9 | 12 h |
| Raw CAMS ≥ 25 for 3 h | 129 | 1.00 | 0.67 | 0.29 | 0 | 18 h |
| CAMS × live-sensor bias ≥ 25 for 3 h | 101 | 0.95 | 0.59 | 0.46 | 0 | 18 h |

Only windows where the episode is **not already under way at issue time** (143 windows, 12 episodes): red POD 0.36 / FAR 0.33 / 7 of 12 episodes missed; amber POD 0.91 / FAR 0.71 / 1 missed; old rule POD 0.91 / FAR 0.83; CAMS × bias POD 0.91 / FAR 0.75 / 0 missed.

What this says:
- The old rule fired in 141 of 164 test windows. Its POD is as good as the amber tier's, but nearly everything it says is false (FAR 0.71). The Peirce score of 0.13 is close to "always alert".
- The new red tier is quiet and precise (FAR 0.12) but **misses a third of the episodes (6 of 32), and most of the genuine onsets (7 of 12)**: it mostly confirms an episode once the sensors show it. Persistence (0.49) is the right comparison for that, and the red tier beats it only modestly (0.64).
- The amber tier recovers the old rule's POD at 0.55 instead of 0.71 false alarms. So the design is: amber = "be alert", red = "it is happening or nearly certain".
- On onset detection the model is **not clearly better than CAMS × sensor bias** (Peirce 0.50 amber vs 0.41, but FAR 0.71 vs 0.75, so a modest gain at best, with 12 episodes).

### Acceptance (proposed bars)
| Bar | Result |
|---|---|
| Episode-hour bias within ±5 µg/m³ (calibrated units) | **Met on the hours the app flags** (CAMS ≥ 50: +0.2 in the back-test, −3.3 in training CV). **Not met on hours selected by the observed outcome ≥ 25: −5.4 µg/m³** (1,192 hours), partly a selection effect but real: heavy hours are under-predicted. |
| POD ≥ the old rule's at a lower false-alarm ratio | **Not met by the red tier** (POD 0.67 vs 0.95). **Met by the amber tier** (0.95 vs 0.95, FAR 0.55 vs 0.71). |
| Quantile coverage within ±5 points | Met in 5-fold, **not met on the future split** (above p90 0.16) |

Additional caveats: P is about the calibrated *sensor* median. Against the EEA station the calibrated median still has a typical error of about 37 % (`sigma_log` 0.37), which is not in P. Hourly value limits are applied to 24 h guideline numbers (screening, not compliance).

## Phase 4: residential heating feature

**Result: it does not enter the model. A close call at one buffer, contradicted by the mechanism check, so it stays a lead.**

### Why this was tried
Solid-fuel home heating is the main winter source: NIMH (2020, via Breathe Cities) attributes 42 % of Sofia's PM emissions to it, Sofia Municipality (2024, via IEEP 2026) about 80 % of winter exceedances, from roughly 32,000 households. District heating (Toplofikatsia) serves about 70 % of households. Since January 2025 a low-emission heating zone bans wood and coal in parts of nine districts where gas or district heating exists (about 1,660 households, so it changes little at city scale). The model's only street features are traffic and terrain.

### Data: what exists and what was done
| Option | Status |
|---|---|
| (a) NSI 2021 census, dwellings by heating fuel | Not found at a useful level. The public NSI dwellings release has totals for the country and the region (Sofia region 755,889 dwellings), no heating breakdown, and no breakdown below region. **To ask:** NSI (nsi.bg, Census 2021 results; regional office Sofia-city) for the table "dwellings by main heating energy source" by municipality and, if it exists, by district/neighbourhood. I could not send this request. |
| (b) Toplofikatsia network / service area | No public polygon found. **To ask:** Toplofikatsia Sofia EAD or the Sofia Municipality Directorate for Energy, or Sofiaplan GIS. Not requested. |
| (c) Low-emission heating zone polygons | A thematic layer exists in the municipality's iSofMap viewer ([GIS-Sofia](https://gis-sofia.bg/en/post/new-in-isofmap-added-low-emission-zones-thematic-map)) under Ordinance of 15.12.2022 (Decision No. 931 of the Metropolitan Municipal Council). No open download or service endpoint is documented, and scraping a commercial viewer is not appropriate. **To ask:** Sofia Municipality / GIS-Sofia for the geometry. Not requested. |
| (d) OSM proxy | **Built** (`make heating` → `data/lowrise.tif`, `data/buildings.tif`): 123,333 OSM buildings in the box; 77,075 count as house-like (house/detached/semidetached/terrace/bungalow/cabin tag, or ≤ 2 levels, or an untagged residential building with a footprint ≤ 200 m²). Rasterised like the other layers (35 m cells), averaged over 250 / 500 / 1000 m buffers. The district-heating area is **not** subtracted (no public polygon), so this is a ceiling on exposure, not an estimate. |

### Tests (`python -m backend.ml.heating_check`, run on the committed code)
**1. Sensor level: rank correlation with the persistent sensor offset** (for scale: traffic +0.22, terrain height −0.21):

| feature | 250 m | 500 m | 1000 m |
|---|---|---|---|
| low-rise share | −0.00 [−0.12, +0.11] | +0.05 [−0.07, +0.17] | +0.10 [−0.02, +0.21] |
| same, traffic and terrain taken out | +0.07 | +0.12 | +0.15 |
| low-rise fraction of built-up area | −0.05 | −0.04 | −0.06 |

Weakly positive, intervals include zero before adjustment, growing with the buffer.

**2. Within-sensor contrast** (winter nights Dec–Feb 22–06 minus summer nights Jun–Aug, per sensor; 242 sensors; the sensor is its own control, so calibration and siting cancel): a heating source should make the contrast larger where low-rise housing is dense. It does not: rank correlation −0.07 / −0.02 / +0.02 for the low-rise share, and **−0.13 to −0.14 (intervals just excluding zero) for the low-rise fraction**; the top-quartile minus bottom-quartile contrast is −0.05 to −0.09 (share) and −0.19 to −0.21 (fraction) log units. The winter-night excess is, if anything, smaller in low-rise areas.

**3. Does the model need it?** Held-out sensors, heating-season hours, within-hour Spearman, sensor-grouped 5-fold, three repeats, street part only:

| street model | all heating-season hours | hours with city level > 15 |
|---|---|---|
| traffic + terrain (current) | 0.170 ± 0.005 | 0.192 ± 0.006 |
| + low-rise share 250 m | 0.165 (−0.005) | 0.193 (+0.001) |
| + low-rise share 500 m | 0.168 (−0.002) | 0.188 (−0.004) |
| + low-rise share 1000 m | **0.210 (+0.041)** | **0.229 (+0.037)** |
| + low-rise fraction 500 m | 0.165 (−0.004) | 0.201 (+0.009) |

Each variant also carries its stagnation interaction (feature × S/100), as the other terms do.

### Acceptance and reading
The bar was +0.05. The best variant gains **+0.041**: **not met**, so it stays out of the model. Two reasons not to read it as a near-miss to be rescued:
- it is the best of five variants tried, and only the 1000 m buffer moves at all. A 1 km average of low-rise cover is a smooth, city-scale surface (outskirts and villages versus the built-up centre), which is the same kind of structure the variogram found (range about 12 km) and the sensor-grouped CV can pick up without it being heating;
- the mechanism test points the wrong way (test 2): heating smoke should show up as a winter-night excess at the sensor itself.

What would settle it: the district-heating polygon (b) or census heating fuel (a). With them the proxy could be restricted to low-rise buildings **outside** the network, which is where the stoves are; as built it also counts low-rise houses on district heating or gas, which dilutes any signal. The construction-site check (above) was also negative, so the street-level model still has only traffic and terrain.

Nothing was added to `features.py` or the model; the rasters are data for the follow-up.
