# The learned PM2.5 model

> **Read this first.** The sections up to "Unified model" describe the model as it was before the work on branch `unified-pm-model`. Since then: PM2.5 is calibrated to the EEA reference scale, the live-sensor correction is regression kriging instead of IDW, the alert is probabilistic, and **the OSM-weighted traffic term was removed** (street model = terrain height only). Where an older section mentions traffic, a calibrated-scale number or IDW, the "Unified model" sections below are current.

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
1. **PM10 instead of PM2.5: now run, see "Step back: more data" below (negative).** The Sensor.Community archive had stopped answering from this machine after a fast download (it works for others; the live API is fine). `backend/data_prep/history.py` now runs slowly, writes to its own file (`--name sensors_pm10`), and stops instead of recording empty days. A waiter script restarts it when the archive answers. Then rerun `python -m backend.ml.construction_check` (PM10 pre-specified check below). Only 9 days of PM10 exist today.
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
First done on the 9 autumn days the archive gave before it stalled (984 station-hours), then **redone on the full PM10 history** (191 days pulled when the archive answered again; 9,969 station-hours in 2026, 88 days, humidity 27–94 %, five stations):

- Same fit form, **RH coefficient −0.43 for PM10 against −0.61 for PM2.5**: same sign, weaker. The first estimate (−0.63 from 9 days) looked like an exact match; it was a short-sample coincidence and is withdrawn.
- Calibrated PM10 average error 7.0 vs raw 14.3 µg/m³. Median calibrated/reference ratio by humidity band: 1.05 / 0.91 / 0.89 / 0.95 / 1.09 / **1.17** (dry to above 90 %): it overshoots in the dampest air.
- By calibrated value, reference ÷ calibrated is 0.75 below 10, 1.01 at 10–25, 0.96 at 25–50, 0.87 above 50: it over-reads at both ends. This PM10 fit feeds only the live WAQI comparison.

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

| | Learned model (final, no traffic term) | Old formula (last measured, with traffic) | CAMS × bias | Persistence |
|---|---|---|---|---|
| Error, hidden sensors + future days (MAE) | **4.64** | 5.67 | 5.29 | 4.86 |
| Error, hidden sensors, all weeks | **7.19** | 10.28 | 9.84 | 7.69 |
| Episode hours (CAMS ≥ 50): MAE / bias | **15.86 / −3.21** | 32.17 / +24.27 | 31.27 / +22.62 | 17.17 / −5.84 |
| Street ranking within an hour | **0.148** (0.127 on the future split) | 0.12 | 0.00 | – |

The learned model keeps its advantage on episode hours and sits within ±5 µg/m³ of bias there. The old hand-weighted formula needed the OSM road-class traffic term; that term was removed from the code later (see "Traffic term removed" below), so it is no longer a baseline in `train.py` and its row is the last measurement. Calibration did not make the street part better, as expected: it scales values, it does not add spatial information.

### Traffic term removed
The street model originally had two features, a traffic term built from OSM road-class weights and terrain height. The traffic term was an assumed quantity (class weights chosen by hand, never measured), it was fitted to a coefficient of only +0.014 log-units per SD, and the project rule is that no assumed values are shown. It was removed from the model (`STATIC_COLS`/`SPATIAL_COLS` now hold terrain height only; `FORMAT` 5), from serving and from the baselines, and everything was retrained. Cost: held-out within-hour street ranking 0.161 → 0.148 (all weeks, unseen sensors), 0.118 → 0.127 (future days); the city level (the part that matters) is unchanged (MAE 7.22 → 7.19). Real measured traffic (Google Routes / TomTom) is a separate live layer; it is not an input to this model.

### Visible effect
**The map and the sensor dots are about 2× more polluted than before on the same day** (median sensor ×1.87 across the history). This is a correction, not a regression: the sensors read about half of the reference station.

## Phase 2: per-sensor offsets and kriging instead of IDW

`make spatial` (`backend/ml/spatial_fit.py`) fits it; `backend/cai/krige.py` serves it; `C.SPATIAL_METHOD = "idw"` brings the old method back (it is also the fallback if `data/spatial.joblib` is missing or was made against another calibration).

### The model
In log1p space, with the city level and the terrain trend taken out:

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
| Variogram (exponential, all hours) | nugget 0.072, partial sill 0.145, **range a = 12.3 km** (practical range about 37 km) |
| by season | heating: 0.083 / 0.168 / 8.6 km · summer: 0.066 / 0.121 / 17.4 km · polluted heating hours (city > 15): 0.106 / 0.174 / 6.5 km |
| Empirical semivariance | below 300 m about 0.06 · 0.6–1.3 km about 0.09 · 4 km 0.11 · 7 km 0.14 · 14 km 0.17 |
| Persistence of the kriged residual | slope on the residual h hours later: 0.89 (0 h) · 0.76 (1) · 0.63 (2) · 0.54 (3) · 0.33 (6) · 0.18 (12) · 0.29 (24) · 0.12 (36) · 0.19 (47). Fitted `rho(h) = 0.18 + 0.72·exp(-h/4 h)`. |

**Reading this: the field carries almost no street-scale information.** The semivariance climbs from 0.06 below 300 m to 0.09 at about 1 km, then keeps climbing slowly to 0.17 at 14 km: only about a sixth of the spatial variance is at scales under a kilometre, the rest is neighbourhood-to-district scale (valley floor and edge, east-west contrasts the terrain trend does not capture). The unstructured part (nugget, 32 % of the fitted sill) cannot be told apart from measurement noise. So sensors can say how a *neighbourhood* differs from the city, not how one street differs from the next. This is what Phase 5 turns into the decision.

The old IDW kept a snapshot residual at full strength for the whole 48 h window. The data say it is mostly gone after 6 h (slope 0.33) and about a fifth remains afterwards.

### Evaluation: leave-one-sensor-out over historical hours
Whole weeks held out (5 folds); offsets, variogram and the interval scale are refitted on the other weeks; at each held-out hour every sensor is hidden in turn and predicted from the others at that hour. The city level is taken as known, which isolates the spatial stage. 2,680 hours, 602k predictions; half the hours are polluted heating-season hours so that this regime is well covered. Median absolute error in µg/m³ / mean within-hour Spearman:

| Held-out hours | level only | level + trend | IDW (old) | kriging, offsets kept | **kriging, offsets removed** |
|---|---|---|---|---|---|
| all (2,680 h) | 3.02 / – | 2.93 / 0.157 | 3.09 / 0.222 | 2.84 / 0.203 | **2.74 / 0.262** |
| heating season (1,645 h) | 5.24 / – | 5.04 / 0.172 | 5.06 / 0.263 | 4.69 / 0.263 | **4.51 / 0.321** |
| summer (1,035 h) | 1.52 / – | 1.46 / 0.132 | 1.60 / 0.157 | 1.48 / 0.109 | 1.48 / **0.167** |
| polluted heating, city > 15 (930 h) | 9.06 / – | 8.71 / 0.190 | 8.61 / 0.282 | 8.00 / 0.303 | **7.74 / 0.345** |
| city > 25 (436 h) | 12.94 / – | 12.60 / 0.180 | 12.26 / 0.284 | 11.36 / 0.334 | **11.00 / 0.359** |

(Median absolute error in µg/m³ / within-hour Spearman, evaluated with the final, terrain-only trend. The first version of this table, with the traffic term, had summer ranking 0.149 vs IDW's 0.159, which was a miss; without the traffic term it is 0.167 vs 0.157.)

- Removing the neighbours' offsets is what makes kriging work: kept, it is only marginally better than the trend.
- **In summer kriging adds nothing over the trend alone for the error** (1.48 vs 1.46 µg/m³), only for the ranking (0.167 vs 0.132). The field is weak at clean levels, where sensor noise (±2 µg/m³) is as large as the signal.
- Caveats: the two trend coefficients were fitted on all sensors (in-sample for them, identical for every method); the neighbour count was set to 24 after looking at the cross-validated numbers for 6/12/24/48 (gain from 12 to 24 is 0.07 µg/m³ and 0.01 in ranking; 48 is the same as 24), so that comparison is slightly optimistic. The hidden sensor's own offset (×1.6 SD) is part of what it is scored against and is unpredictable; this puts a ceiling on every correlation here.

**Uncertainty.** Per-street SD (log1p units) = scale × √(nugget + offset variance + kriging variance), shrinking the explained part with the lead through ρ². The raw SD gave 80 % intervals that held 0.83 (heating) and 0.88 (summer) of the hidden readings; a per-season factor fitted on the training weeks (heating 0.88, summer 0.63; the summer noise is smaller at clean levels) brings them to **0.796 (heating), 0.798 (summer)**, 0.777 in polluted heating hours and 0.772 above 25 µg/m³. The level part of the uncertainty is added in Phase 3.

### Acceptance
| Bar | Result |
|---|---|
| Leave-one-sensor-out median error below IDW over historical hours | **Met** in every cut (all 2.74 vs 3.09; heating 4.51 vs 5.06; summer 1.48 vs 1.60; polluted heating 7.74 vs 8.61) |
| Within-hour Spearman not lower than IDW | **Met** in every cut: overall 0.262 vs 0.222, heating 0.321 vs 0.263, summer 0.167 vs 0.157, polluted heating 0.345 vs 0.282 |
| 80 % intervals cover 75–85 %, heating and summer separately | **Met** (0.796 and 0.798) after the per-season scale; before it, summer was 0.88 |

Live snapshot (`/api/validation`, one clean autumn evening, 273 sensors, re-measured with the final model): kriging ρ 0.02 / median error 2.56, IDW 0.01 / 2.82, trend only 0.04 / 2.35. Nothing beats the trend on a clean day, in line with the summer rows.

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
- The new red tier is quiet and precise (FAR 0.12) but **misses a third of the episode windows (14 of 43), i.e. 6 of 32 distinct episodes, and most of the genuine onsets (7 of 12)**: it mostly confirms an episode once the sensors show it. Persistence (0.49) is the right comparison for that, and the red tier beats it only modestly (0.64).
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

### Tests (`python -m backend.ml.heating_check`, run on the final terrain-only model; an earlier run with the traffic term in the baseline gave the same picture: best gain +0.041)
**1. Sensor level: rank correlation with the persistent sensor offset** (for scale: terrain height −0.21):

| feature | 250 m | 500 m | 1000 m |
|---|---|---|---|
| low-rise share | −0.00 [−0.12, +0.11] | +0.05 [−0.07, +0.17] | +0.10 [−0.02, +0.21] |
| same, terrain taken out | +0.07 | +0.11 | +0.15 |
| low-rise fraction of built-up area | −0.05 | −0.04 | −0.06 |

Weakly positive, intervals include zero before adjustment, growing with the buffer.

**2. Within-sensor contrast** (winter nights Dec–Feb 22–06 minus summer nights Jun–Aug, per sensor; 242 sensors; the sensor is its own control, so calibration and siting cancel): a heating source should make the contrast larger where low-rise housing is dense. It does not: rank correlation −0.07 / −0.02 / +0.02 for the low-rise share, and **−0.13 to −0.14 (intervals just excluding zero) for the low-rise fraction**; the top-quartile minus bottom-quartile contrast is −0.05 to −0.09 (share) and −0.19 to −0.21 (fraction) log units. The winter-night excess is, if anything, smaller in low-rise areas.

**3. Does the model need it?** Held-out sensors, heating-season hours, within-hour Spearman, sensor-grouped 5-fold, three repeats, street part only:

| street model | all heating-season hours | hours with city level > 15 |
|---|---|---|
| terrain only (current) | 0.169 ± 0.007 | 0.188 ± 0.007 |
| + low-rise share 250 m | 0.163 (−0.006) | 0.188 (−0.000) |
| + low-rise share 500 m | 0.168 (−0.001) | 0.186 (−0.002) |
| + low-rise share 1000 m | **0.213 (+0.045)** | **0.230 (+0.042)** |
| + low-rise fraction 500 m | 0.169 (−0.000) | 0.202 (+0.015) |

Each variant also carries its stagnation interaction (feature × S/100), as the other terms do.

### Acceptance and reading
The bar was +0.05. The best variant gains **+0.045**: **not met**, so it stays out of the model. Two reasons not to read it as a near-miss to be rescued:
- it is the best of five variants tried, and only the 1000 m buffer moves at all. A 1 km average of low-rise cover is a smooth, city-scale surface (outskirts and villages versus the built-up centre), which is the same kind of structure the variogram found (range about 12 km) and the sensor-grouped CV can pick up without it being heating;
- the mechanism test points the wrong way (test 2): heating smoke should show up as a winter-night excess at the sensor itself.

What would settle it: the district-heating polygon (b) or census heating fuel (a). With them the proxy could be restricted to low-rise buildings **outside** the network, which is where the stoves are; as built it also counts low-rise houses on district heating or gas, which dilutes any signal. The construction-site check (above) was also negative, so the street-level model still has only terrain height.

Nothing was added to `features.py` or the model; the rasters are data for the follow-up.


## Phase 5: street level or neighbourhood level?

**Recommendation: neighbourhood level (250 m cells) is the default; street colouring stays as an opt-in "indicative" layer. The router only says "cleanest" when the difference is larger than the model's uncertainty.**

### Evidence
Same held-out protocol as Phase 2 (whole weeks held out, every sensor hidden in turn and predicted from the others, 80 % intervals scaled per season), heating-season hours with a city level above 15 and above 25 µg/m³ (`python -m backend.ml.street_vs_area`):

| Within-hour Spearman at a hidden sensor | city > 15 (930 h) | city > 25 (436 h) | all heating hours |
|---|---|---|---|
| trend only (terrain) | 0.200 | 0.177 | 0.181 |
| **street-level field** (what the street map shows) | **0.349** | **0.355** | **0.322** |
| field averaged over the 250 m square containing the sensor | 0.347 | 0.354 | 0.320 |
| ... 500 m square | 0.348 | 0.355 | 0.320 |
| ... 1 km square | 0.348 | 0.355 | 0.321 |
| ... 2 km square | 0.340 | 0.349 | 0.314 |
| 80 % intervals hold | 0.78 | 0.77 | 0.80 |

(Both street and cell values are computed without the hidden sensor, with the same terrain trend added; the cell value is the mean of the field at a 3 × 3 grid of points in the square.) **Averaging the field over 250 m squares costs nothing**, and even 2 km squares cost 0.01. So the skill the sensors support lives at neighbourhood scale (the variogram says the same: range about 12 km, only a sixth of the variance below 1 km).

Can anything rank streets that are close together? Share of sensor pairs in the same hour (heating season, city > 15) whose terrain-trend order matches the order of their readings (0.5 = chance, ties dropped):

| distance | 0–250 m | 250–500 m | 0.5–1 km | 1–2 km | 2–5 km |
|---|---|---|---|---|---|
| pairs | 6,209 | 20,942 | 71,395 | 262,071 | 1,494,849 |
| concordance | 0.55 | 0.51 | 0.47 | 0.54 | 0.56 |

Pairs share sensors and hours, so the real uncertainty is larger than the naive 0.006–0.0004. Nothing is above 0.56 at any distance, and the shortest distances are no better than the longest.

Intervals and calibration: 80 % intervals cover 0.78 / 0.77 in polluted heating hours (target 0.75–0.85); city-level 10 %/90 % quantiles cover well in the week-blocked evaluation but drift too narrow on the future split (Phase 3).

### The decision rule, and where I departed from it
The proposed rule was: within-hour Spearman below 0.3 in polluted heating-season hours → neighbourhood default. The measured value is **0.35, above the line**, so by the letter the street map stays. I did not follow it, because the rule assumes the Spearman measures street-level skill, and the direct test above shows the same skill from a 250 m cell map, which a street map cannot beat. If the street-by-street differences carried information, the street field would outrank its own cell average; it does not. This is a judgement; the street layer is kept (opt-in) so it can be compared.

### What changed in the app
- **CAI map**: defaults to 250 m cells (`/api/cai/areas`, the same cells the network view uses); a switch "Neighbourhoods | Streets (indicative)" brings back the street colouring and the "Why is this red?" panel, with a warning that street detail is not supported.
- **Router** (`backend/cai/route_confidence.py`): the difference in mean PM2.5 between the fastest route and each alternative is compared with its uncertainty: the unstructured part of each street (nugget + offset variance) averaged over independent 150 m pieces of the *unshared* stretches only (shared streets cancel), plus the spatially correlated error between the two stretches (from the variogram), times the per-season interval scale. Distinguishable = difference beyond 1.28 standard errors. If no alternative is distinguishable, the route panel says **"All routes have about the same air right now"** and hides "Open cleanest in Google Maps". The 150 m piece length is an assumption the sensors cannot check.
  Tried on 36 route requests (6 trips × hours 0/6/12 × live fixture and the winter demo): in 18 the cleanest route was the fastest route, in the others it differed in mean PM2.5 by **0.0–0.8 %** against an uncertainty of **4–10 %**. **None was distinguishable.** With this model, routing for cleaner air is within the noise on typical trips; the "cleanest" route changes the path but not the exposure the model can resolve.
- **Validation card** (`/api/validation` → `trust`): sensors against the EEA station (raw vs calibrated error, ratio by humidity, typical error ±37 %, and the note that one station anchors it), the spatial stage (ranking and error vs the old method in polluted winter hours, 80 % coverage, offsets ±61 %, pattern scale), and the banner back-test (red tier and old rule, caught episodes and false alarms). The old "physics formula" rows are gone.
- **Districts**: advice, dashboard and `/api/districts` carry a per-district level and chance, from the city forecast moved by the district's offset-adjusted sensors (Phase 3).

### Acceptance
| Bar | Result |
|---|---|
| Report Spearman and interval coverage for polluted heating-season hours (> 15, > 25) | Done, table above |
| Neighbourhood default if Spearman < 0.3 | Rule not triggered (0.35); neighbourhood default adopted anyway on the cell-versus-street evidence |
| Router offers "cleanest" only beyond combined uncertainty | Implemented; none of 36 sample requests qualified |
| Validation card updated with reference-station agreement | Done |

## Phase 6: limits and what is still missing

- **One reference station.** Hipodruma is the only usable PM2.5 anchor, with 13 sensors within 1.5 km, 1,991 hours of 2026, three hours above 50 µg/m³. Everything above 40 µg/m³ is extrapolation. The calibrated city value is typically ±37 % of the station.
- **The history is a sample**: one week in seven plus the 14 worst-CAMS episodes, so episode rates in the back-test are higher than climate, and the 2024/25 heating season used to choose the red threshold has only 5 non-episode windows.
- **Future-days drift**: level intervals are too narrow on the last quarter of the history (above-p90 0.16).
- **No heating map**: the strongest known winter source has no usable feature; the OSM proxy fails its bar. Needs census heating-fuel data, the district-heating polygon, or the low-emission-zone geometry (Phase 4 lists whom to ask).
- **Observed heavy hours are under-predicted** (−5.4 µg/m³ on hours whose outcome was ≥ 25; an outcome-selected statistic, but real for the alert's recall: the red tier misses 6 of 32 episodes in the test winter).
- **PM10 calibration** (88 days, five stations) over-reads in the dampest air and at low concentrations; the live WAQI check reads 1.38× (range 1.03–1.92) against 0.54× raw. WAQI's PM10 is converted from AQI, so neither number is firm.
- **Not done**: ELAPSE surfaces (no public download), NIMH station forecasts, census heating fuel / district-heating area / low-emission-zone geometry.


## Step back: more data, and a direct model

Prompted by the objection that one city-wide weather point and one city median cannot describe a city whose parts differ in traffic, temperature, buildings and vegetation ("Sofia is 20 degrees, so Varna is 20 degrees"), the data were re-examined, new local sources were pulled, and the model was rebuilt the way the objection suggests: inputs = coordinates, hour, the nearest sensors' readings, weather, static features, output = what a sensor there would read. Result: **the objection is right about the inputs and wrong about the payoff**. Local inputs exist and differ, but the place-dependent signal they could explain is small next to a stable sensor-specific offset that no input predicts.

### Local conditions do differ
Open-Meteo for six places on 2026-01-01 22:00 (a polluted inversion night; Hipodruma 63 µg/m³): boundary-layer height 20 / 65 / 20 / 30 / 80 / 220 m (centre / Lyulin / Mladost / Nadezhda / Kremikovtsi / Vitosha foot), wind 0.5 to 13.5 km/h, humidity 51 to 87 %, temperature −1.8 to −5.5 °C. Over winter nights at 35 grid points, the spread across the city is large relative to the variation in time: boundary-layer height 87 m (SD across points) vs 167 m (SD in time of the city mean), wind 3.0 vs 3.3 km/h, humidity 6.9 vs 7.9 %, CAMS PM2.5 13.1 vs 14.1 µg/m³.

### New data pulled
| Data | What | Size / coverage | Command |
|---|---|---|---|
| Weather and CAMS grid | Open-Meteo historical forecast + CAMS PM2.5/PM10/NO2 at 35 points over Sofia | 672,000 hours, 2024-08 to 2026-10 | `python -m backend.data_prep.weather_grid` |
| Local temperature / humidity / pressure | the BME280, DHT22, SHT3x next to the PM sensors (Sensor.Community archive) | 203 of 258 outdoor PM locations; 574,670 sensor-hours over the 130 history days | `python -m backend.data_prep.history_env` |
| Building height, volume, population | JRC GHSL (GHS-BUILT-H, -V, GHS-POP), 3 arc seconds (~90 × 68 m), CC BY 4.0 | 3 small rasters in `data/ghs_*.tif` | `python -m backend.data_prep.ghsl` |
| PM10 history | the Sensor.Community archive, same days as PM2.5 | 191 days, 697k sensor-hours | `python -m backend.data_prep.history --name sensors_pm10 --same-days --no-series` |

Considered and left out: EUBUCCO per-building heights (about 57 % are ML-estimated from footprint attributes, so they add little over the OSM footprints already used), satellite NO2/aerosol (5 km and coarser), the TNO wood-combustion inventory (7 km), the Sofia open-data portal (no usable datasets found). `backend/ml/local_features.py` turns the first three into per-place, per-hour inputs (the target's own co-located temperature sensor, if any, is never used).

### The direct model (`backend/ml/direct_model.py`)
One gradient-boosted model: coordinates, 8 nearest *other* sensors' readings with distance and direction, neighbour means inside 5 radii, weather and CAMS at the target and issue hours, static features, optional local inputs. Scored on sensors never trained on in weeks never seen ("bi-fold": sensors and weeks both split in five, fold k tests on sensor group k in week group k, trains on rows outside both). Hidden sensor predicted at lead 0, same hours and sensors as the kriging chain refitted on the same training weeks. Median error in µg/m³ / within-hour Spearman:

| | all hours | polluted heating (city > 15) | 80 % interval holds |
|---|---|---|---|
| terrain trend alone | 2.94 / 0.16 | 8.96 / 0.21 | – |
| **kriging chain (current)** | **2.78 / 0.25** | **8.07 / 0.34** | 0.79 |
| direct model, first version (anchored on CAMS) | 3.90 / 0.10 | 10.57 / 0.18 | 0.64 |
| ... without coordinates | 3.83 / 0.08 | 10.33 / 0.18 | – |
| ... without coordinates and static features | 3.81 / 0.10 | 10.30 / 0.20 | – |
| ... without coordinates, static features and weather | 4.83 / 0.14 | 13.61 / 0.24 | – |
| direct model, anchored on the neighbours' median (a design fix) | 3.73 / 0.06 | 10.32 / 0.15 | – |
| ... plus local weather grid, nearby temperature sensors, GHSL buildings | 3.76 / 0.07 | 10.46 / 0.12 | – |

It is **worse than the plain median plus terrain** at every cut. Coordinates and static features hardly matter (removing them changes the error by 0.1); the nearest sensors' distances and directions already fingerprint a place, so the trees memorise it. The first version's CAMS anchor was a design flaw (the trees had to rebuild the city level); anchoring on the neighbours' median helped a little and did not change the verdict. The local inputs add nothing here either. These runs used the median model only (no intervals), except the first.

### ML on top of kriging (`backend/ml/stack_eval.py`)
The standard remedy: keep kriging and let the trees learn what is left over, trained on the reading with the sensor's own offset removed (otherwise they learn offsets from features that fingerprint the place). Development on folds 0 and 1 only looked like a small gain (median error −2 %, ranking +0.025); **on the untouched folds 2–4 it does not replicate**:

| Folds 2–4, hidden sensors | kriging | + ML, local inputs | + ML, no local inputs | knowing the sensor's own offset (ceiling) |
|---|---|---|---|---|
| all: error / ranking | 2.99 / 0.264 | 3.04 / 0.243 | 3.03 / 0.256 | 1.62 / 0.778 |
| heating | 5.54 / 0.330 | 5.75 / 0.301 | 5.68 / 0.316 | 2.61 / 0.808 |
| polluted heating (city > 15) | 8.58 / 0.350 | 8.95 / 0.316 | 8.83 / 0.337 | 4.07 / 0.797 |
| city > 25 | 11.97 / 0.358 | 12.54 / 0.315 | 12.31 / 0.340 | 6.12 / 0.769 |

The local weather grid, the nearby temperature sensors and the GHSL buildings do not improve the held-out prediction; the ML step is slightly worse than kriging alone.

### Is it too little data? A learning curve
ML on top of kriging, development folds 0 and 1, median error (µg/m³, all hours) of kriging alone vs kriging plus the ML correction, as the trees get more training hours or more training sensors:

| training data | kriging | + ML | change |
|---|---|---|---|
| 100 hours | 2.534 | 2.515 | −0.8 % |
| 400 hours | 2.497 | 2.436 | −2.4 % |
| 700 hours | 2.494 | 2.441 | −2.1 % |
| 1,500 hours | 2.494 | 2.441 | −2.1 % |
| 700 hours, 25 % of the training sensors | 2.494 | 2.534 | +1.6 % (worse) |
| 700 hours, 50 % of the training sensors | 2.494 | 2.479 | −0.6 % |
| 700 hours, all training sensors | 2.494 | 2.441 | −2.1 % |

More hours of the same sensors stop helping at about 400. More sensors help steadily (from worse than nothing to a 2 % gain), but even doubling them would plausibly stay in single digits, against the 50 % that knowing each sensor's offset would give (and the gain did not replicate on the held-out folds 2-4). The limit is not the number of rows; it is that each new sensor brings its own unknowable offset.

### Why: the sensors' offsets are not about the place
The last column is the key. If the model knew each hidden sensor's persistent offset (its stable deviation from what the trend and the neighbours predict), the error would halve and the ranking would jump from 0.25–0.35 to about 0.8. So about 85 % of the ranking among sensors is that one number per sensor. Is it the air of the place, or the instrument? Offsets of neighbouring sensors are **uncorrelated**:

| distance between two sensors | pairs | correlation of their offsets |
|---|---|---|
| 0–150 m | 17 | +0.05 |
| 150–300 m | 56 | +0.07 |
| 300–600 m | 174 | +0.02 |
| 0.6–1.2 km | 604 | −0.04 |
| 1.2–2.5 km | 2,446 | +0.01 |
| 2.5–6 km | 11,211 | +0.01 |

Predicting a hidden sensor's offset from its 1, 3 or 8 nearest sensors' offsets gives R² of −0.73, −0.39, −0.26 (worse than the average). If offsets were local pollution (a heated street, a canyon), nearby sensors would share part of it; they do not. They look like instrument and siting differences (calibration, inlet height, enclosure, sun on the housing), which is also why they are stable (r = 0.85 between halves of the history). (Caveat: the 0–150 m bin has only 17 pairs.) This also explains why no feature, however local, can predict them: they are not a function of place.

What is predictable is the hour-to-hour spatial structure after the offsets: neighbourhood-scale, 80 % intervals holding, and that is what the kriging chain models.

### Other checks run
- **Measured NO2 as an input to the PM2.5 level (the NO2 card's idea, tested):** adding the mean of the five EEA NO2 stations at the issue hour changes the level model's average error from 2.351 to 2.339 µg/m³ and makes episodes slightly worse (8.60 → 8.69). Adding NO2 at the target hour (2.263) is a nowcast only, not available for a forecast, and the sensors already give the level at lead 0. **Not a PM2.5 input.** (dishai-34's separate NO2 model and card stand on their own.)
- **PM10 construction-dust check, pre-specified earlier, now run on 191 days of PM10:** sensors within 300 m of sites cleared since last summer (24 sensors) vs sensors with no mapped site within 300 m (47): workday-minus-weekend daytime gap +0.043 log units, 95 % interval [−0.095, +0.169]. **Includes zero: no site-activity term.** Also nothing for bare sites, any mapped site, or the green control; PM10/PM2.5 near sites +0.593 vs +0.574 elsewhere.
- **PM10 calibration** refit on the full history (see the Phase 1 cross-check): RH coefficient −0.43, not the −0.63 first seen on 9 days.

### What this means for the design
- The chain stays: calibrated level, kriging with offsets removed, neighbourhood-level display, honest intervals. The direct model and the ML-on-kriging step are kept as experiments (`direct_model.py`, `stack_eval.py`), not shipped.
- To make street-level claims possible, the instrument part of the offsets has to be separated from the place part. That needs data not available here: side-by-side runs of a few citizen sensors next to a reference station (or each other) to calibrate each sensor individually, or sensor metadata (inlet height, enclosure, location type). With per-sensor calibration, the sensors' remaining differences would be the place's, and the local inputs pulled here would have something to explain.
- The local weather grid and the sensors' temperatures are still the right inputs for the *level* forecast (boundary-layer height 20 vs 220 m on the same night); that has not been tested yet and is the next experiment: city level from local rather than central weather.


## More datasets: the Kaggle Sofia archive and the others

Six sources were offered. Only one of them can inform a Sofia model.

| Source | What it is | Verdict |
|---|---|---|
| `air-quality-dataset_Sofia.zip` (Kaggle, Sensor.Community Sofia) | 50 monthly CSVs, July 2017 to July 2019: SDS011 PM readings and BME280 weather every few minutes, 15 GB uncompressed | **Used.** 4.5 M sensor-hours, 487 sensors inside the model box (77 in July 2017, ~380 from 2019), winters with raw PM2.5 up to 327 µg/m³ (p99, Jan 2018). Stream-reduced to hourly means with `backend/data_prep/kaggle_sofia.py` (read as data only, nothing extracted to disk). 83 sensor ids are also in today's network. No indoor flag; the BME280 winter-night temperature (median 1.7 °C, 1 of 394 locations above 12 °C) shows they are outdoors. |
| `air-quality-dataset_1.ipynb` (Kaggle notebook) | analysis of a 500-row table (PM2.5, PM10, NOx, NO2, SO2, VOCs, CO, CO2, CH4, temperature, humidity, wind direction, location type, source label) | **Not usable.** No coordinates, no timestamps, 500 rows; values look generated. It was read as text, never executed. |
| `air-quality-dataset_2.zip` | `dataset.csv`, 7 unlabelled numeric columns, ~50 kB | **Not usable.** No labels, location or time (it looks like a gas-sensor-array readout). |
| `air-quality-dataset_3.zip` | `Air Quality.csv`: the UCI Air Quality set, one multi-sensor device in an Italian city, March 2004 to April 2005, 9,357 hours | **Not usable.** One place, no PM, 2004. |
| Hugging Face `neuralsorcerer/air-quality` | 100 M rows, 1,000 sites | **Not usable.** Its own card says it is **fully synthetic** ("inspired by Kolkata", coordinates and event effects are constructs) and "not a reconstruction of historical measurements". Training on it would teach the generator's rules. |
| Hugging Face `DeadCardassian/PM25Vision` | 11,114 street-level photos (Mapillary) labelled with WAQI PM2.5 AQI at 3,261 stations worldwide, 2014–2025; image models reach R² 0.23–0.55 | **Not used.** Photos, not hourly place-level concentrations; matching Sofia sensors would need Mapillary imagery at each, and the label is a daily AQI. |

### What the 2017–2019 Sofia data say (`python -m backend.ml.old_sofia`)
Both eras on the raw sensor scale, no terrain trend, same procedure:

| | 2017–2019 | 2024–2026 |
|---|---|---|
| sensors with ≥ 100 h / hours with ≥ 40 sensors | 482 / 18,097 | 261 / 3,115 |
| SD of the persistent offset (log1p) | 0.29 | 0.57 |
| semivariance of the residual after offsets: below 300 m / at 1 km / at 13 km | 0.10–0.12 / 0.11 / 0.21 | 0.07–0.11 / 0.11 / 0.26 |
| variogram fit: nugget / partial sill | 0.092 / 0.242 | 0.090 / 0.327 |
| correlation of neighbouring sensors' offsets: 0–150 m (pairs) / 150–300 m / 0.6–1.2 km | +0.20 (93) / +0.09 / +0.07 | +0.38 (17) / +0.09 / +0.00 |

- **The spatial structure replicates**: the same shape, small short-range structure and a slow rise to city scale, in a different era with a different, larger set of sensors.
- **Offsets are not completely independent between close neighbours**, as the raw-scale numbers show (+0.20 at 0–150 m in 2017–19). This softens the earlier statement ("uncorrelated"; +0.05 for 17 pairs, after removing the terrain trend and calibrating): at most a fifth of the offset covariance is shared at under 150 m, and it is gone beyond a few hundred metres, so the offsets remain mostly sensor-specific. Part of the short-range correlation is plausibly the terrain trend (removed in the earlier test) and neighbours installed together.
- **Offsets are stable for months and fade over years.** Correlation of a sensor's offset between calendar quarters: 0.73 (next quarter) → 0.47 (two quarters) → 0.5 (a year) → 0.3 (1½–2 years) in 2017–19; 0.87 → 0.75 → 0.67 → 0.7 in the current era. For the 83 sensor ids present in both eras (7 years apart) the correlation is 0.15; for the 47 that stayed within 100 m it is **0.06**. So a sensor's offset is not a property of its place: at the same address, seven years later, it is unrelated. It is not a lifelong property of the device either (devices drift or are replaced under the same id).
- **Time-local offsets**: using offsets from ±90 days around the test hour (not just the whole history) improves hidden-sensor ranking on the untouched folds (0.249 → 0.271 all hours; 0.316 → 0.336 polluted winter hours; error −0.2 % to −2 %). But it needs future data. The deployment-like version (offsets from the W days *before* the hour only, W = 60, 120, 240) gives **no gain** (0.249 → 0.264 / 0.256 / 0.242; polluted winter 0.316 → 0.317 / 0.322 / 0.321, errors within ±0.5 %). Nothing changed in the app; an app that keeps accumulating live readings could re-estimate offsets continuously (`offset_window_eval.py`).

### Does training on the old data help? (`python -m backend.ml.pool_eval`)
A weather-free local model that works in both eras: kriged backbone (offsets removed) plus trees on static features of the place (terrain, canopy, NDVI, surface heat, construction, low-rise housing, GHSL buildings and population), the four nearest neighbours' residuals and distances, kriging variance, hour, month. Coordinates are not features. Tested on 2024–26 sensors and weeks never trained on (folds 2–4; folds 0–1 gave the same picture). Median error (raw µg/m³) / within-hour Spearman:

| 2024–26 hidden sensors | kriging alone | trees trained on 2024–26 | trees trained on 2017–19 only | trees trained on both |
|---|---|---|---|---|
| all hours | 1.830 / 0.237 | 1.823 / 0.230 | 1.851 / 0.226 | 1.828 / 0.234 |
| heating | 3.157 / 0.328 | 3.169 / 0.313 | 3.191 / 0.320 | 3.146 / 0.320 |
| summer | 0.967 / 0.097 | 0.960 / 0.100 | 0.973 / 0.078 | 0.959 / 0.099 |
| polluted heating (city > 15) | 7.991 / 0.413 | 8.101 / 0.403 | 8.030 / 0.408 | 8.064 / 0.408 |

**No variant beats kriging, and adding 487 sensors from another era does not help.** Two years of other sensors, ten times the rows, and the correction still adds nothing: the limit is what the inputs can say about a place, not the number of training rows. (No reference data exist for 2017–19 in this repository: the EEA API serves AirBase only to 2012, the verified dataset is empty for Bulgaria, and the old download endpoint now needs authorisation; so these years could not be used to calibrate or to extend the level model, which also needs CAMS, not available for 2017–19.)

### Against the place's own variation
Scored against the sensors' readings with their own persistent offset removed (the place's hour-to-hour variation, without the instrument bias), the same kriging predictions look much better: within-hour Spearman 0.455 in the heating season and 0.462 in polluted winter hours (0.330 / 0.350 against the raw readings), median error 4.6 µg/m³ in polluted winter hours (vs 8.6). The ML correction still does not beat kriging on that target (0.432). So a model of *local* variation exists and works moderately; the raw-reading scores understate it because each sensor carries an instrument bias that no input can predict.


## Multi-city calibration (`python -m backend.data_prep.multicity pull`, `python -m backend.ml.calibrate_pooled`)

The Sofia calibration rests on one reference station (13–14 sensors, 91 hours above 25 µg/m³, 3 above 50). The same SDS011 sensors sit next to EEA PM2.5 stations across Europe, so the calibration was refitted on many cities and scored on Sofia, and the other way round. Data (2026, the same sample days as the Sofia history, sensors from the Sensor.Community archive within 1.5 km of each station, at least 3 per hour, RH and temperature from Open-Meteo at the station): **52 EEA stations in 29 cities** (DE 33, BE 8, BG 3, NL 2, MK 2, GR, RO, SE, SK), 96,309 station-hours, **1,477 hours above 50 µg/m³** (Sofia alone: 3). Austria, Poland, Spain and Italy could not be matched (their EEA file names do not contain the station code). Whole weeks are held out in every score.

**Scored on Sofia (Hipodruma):**

| fit | average error | bias | r | calibrated ÷ reference by humidity band | error / bias when the station reads > 25 |
|---|---|---|---|---|---|
| raw sensors | 5.16 | −4.78 | 0.742 | 0.37–0.76 | 15.1 / −15.1 |
| Sofia only (the app's calibration) | **3.22** | −0.41 | 0.797 | 0.97–1.04 | **8.0 / −6.8** |
| **other cities only (never saw Sofia)** | 3.31 | +0.10 | 0.787 | 0.97–1.14 | 8.5 / −7.7 |
| all cities pooled | 3.31 | +0.03 | 0.787 | 0.97–1.13 | 8.8 / −8.3 |

**A calibration fitted without any Sofia data is within 3 % of the Sofia-specific one**, and the correction removes the same ~40 % of the error. **Leave one city out, over 28 cities:** raw 5.35 → fitted on the other cities 3.17 (−41 %) → fitted on the city itself 2.71; the Sofia-only fit applied elsewhere gives 3.54, worse than the pooled fit (3.17), so the Sofia fit is slightly Sofia-specific and the pooled one travels better.

**What generalises:** the humidity term, `−0.67` pooled vs `−0.62` Sofia; the log-linear form; the ~2× under-reading (raw ÷ reference median 0.3–0.6 in every country). **What does not:** the Sofia fit's temperature term (+0.17) and heating-season term (+0.31) are not supported elsewhere (pooled −0.06 and +0.005), a hint that they partly absorb Sofia-specific effects of a single station.

**The high end, which one station could not test.** Reliability by the calibrated value (leave-one-city-out, all cities): when the calibrated reading is above 25 µg/m³ the station reads a median **1.06×** that value (6,942 hours); above 50, **0.95×** (786 hours). A flexible model (gradient boosting) gives 1.03 and 1.04 and 7 % lower error overall (2.96 vs 3.17), but no better on Sofia (3.33), so the simple form stays. The "−8 µg/m³ bias when the station reads > 25" is selection on the outcome (regression to the mean), not a bias given the sensor reading.

**Decision.** No change to the app's calibration: the pooled fit is not better on Sofia (3.31 vs 3.22). What changes is the confidence: the form, the humidity slope and the behaviour above 50 µg/m³ are now backed by 52 stations instead of one, and the earlier caveat that values above ~40 µg/m³ were pure extrapolation is replaced by "checked against other cities". Caveats: 34 of the 52 stations are German, whose aerosol differs from Sofia's winter smoke; the Sofia heating-season term is untested outside Sofia; the pooled test uses 2026 only.
