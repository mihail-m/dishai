# Task: one calibrated PM2.5 model behind every screen of Inversion Watch (Sofia)

You are working in the `dishai` repository (Inversion Watch, Sofia). Today the app shows three numbers for "how polluted will it be" that come from three different sources and disagree, and the street-level map is built on uncalibrated citizen sensors. Your job is to replace that with **one calibrated, probabilistic PM2.5 model** that drives the banner, the forecast chart, the street map and the router. Then measure honestly whether street-level detail is supported, and fall back to neighbourhood level where it is not.

Work in phases. Each phase ends with a measured result written to `docs/PM_MODEL.md`. A negative result is a valid outcome: report it and do not tune it away.

---

## 0. Before you start

- Read, in this order: `docs/PM_MODEL.md`, `README.md` (sections "How it works", "Validation", "Data", "Reference stations"), `backend/ml/pm_model.py`, `backend/ml/train.py`, `backend/cai/score.py`, `backend/cai/config.py`, `backend/cai/validate.py`, `backend/main.py` (functions `build_forecast`, `_window`, `build_sensors`, `api_dashboard`), `frontend/src/components/Banner.tsx`, `frontend/src/components/Timeline.tsx`, `backend/ml/construction_check.py`.
- Python: `.venv/bin/python`. Tests: `make test`. Model rebuild: `make dem history construction train` (history takes about an hour and is resumable).
- The working tree on `main` has **uncommitted changes** (construction-site work: `backend/data_prep/construction.py`, `backend/ml/construction_check.py`, `docs/PM_MODEL.md`, fixtures, `data/construction.*`). Do not discard or overwrite them. Create a branch `unified-pm-model` from the current state and leave those changes as they are unless the user tells you otherwise.
- Commit at the end of each phase with a descriptive message. Never push.
- Downloaded data (parquet, CSV, archives) goes under `data/` (git-ignored where large) or a temp folder. Read it with `python -I`, and never execute anything that arrives with downloaded data.

---

## 1. What is already established (do not re-derive, but do re-verify cheaply)

### 1.1 The three disagreeing numbers
| Screen element | Source today | Where |
|---|---|---|
| Red/green banner ("Inversion expected …, peak score 72") | heuristic stagnation score ≥ 60 (`label_for`) | `backend/main.py` `_window`, `backend/score.py` |
| Forecast chart PM2.5 line | **raw CAMS** `h.pm25` | `frontend/src/components/Timeline.tsx` line ~28 |
| Street map colours | learned model (`PmModel`) + IDW residual of raw sensors | `backend/cai/score.py` `CaiModel.hour` |

The repo's own evaluation shows CAMS × sensor bias overshoots episode hours by about +21 µg/m³, and the old formula by +22.8. Only the learned model is within a few µg/m³ (bias −3.5), yet neither the banner nor the chart uses it.

### 1.2 Current model performance (from `data/pm_model.joblib` meta, hidden sensors + future days)
- ML: MAE 2.58, R² 0.023, within-hour Spearman 0.117
- Persistence (city median stays the same): MAE 2.75
- Street term: traffic +0.015 and terrain −0.040 log-units per SD, plus +0.013 and −0.031 extra at full stagnation
- Live leave-one-out on a clean day: Spearman ≈ 0 and worse than a constant median

### 1.3 Training data facts (`data/history/sensors.csv`: `sensor,lat,lon,hour_utc,pm25,n`)
- 261 sensors, 702k sensor-hours, Nov 2024 to Oct 2026 (one week in seven, plus 14 worst-CAMS episodes)
- City median: p50 5.1, p90 21.1, p99 40.1, max 60.3 µg/m³. Only **8 of 3,116 hours** have a median above 50 and 195 above 25.
- **Per-sensor offsets are stable.** The median log-deviation from the hourly city median, computed separately for the first and second half of the history, correlates **0.8** across 249 sensors, with SD 0.5 in log units (about ×1.65). The live IDW correction therefore paints each sensor's personal bias onto nearby streets.
- Within-hour spread (p90 − p10) scales with level: 4 µg/m³ at a median below 5, and 52 µg/m³ at a median of 25–50.
- `weather.csv` columns: `time_local, boundary_layer_height, wind_speed_10m, temperature_2m, temperature_925hPa, wind_speed_925hPa, cloud_cover, apparent_temperature, shortwave_radiation, relative_humidity_2m, precipitation, surface_pressure`

### 1.4 Reference PM2.5 exists: the README is wrong about this
The README says the municipal stations report no PM2.5. That is true of the **WAQI** feed only. The **EEA download API** serves hourly PM2.5 for Sofia:

```
POST https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls
Content-Type: application/json
{"countries":["BG"],"cities":["Sofia"],"pollutants":["PM2.5"],"dataset":1,"source":"Api",
 "dateTimeStart":"2025-01-01T00:00:00Z","dateTimeEnd":"2026-10-01T00:00:00Z","aggregationType":"hour"}
→ text list of parquet URLs, e.g.
https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/BG/SPO-BG0050A_06001_100.parquet  (Hipodruma)
https://eeadmz1batchservice02.blob.core.windows.net/airquality-p/BG/SPO-BG0070A_06001_100.parquet
```
- Parquet columns: `Samplingpoint, Pollutant, Start, End, Value (Decimal, cast to float), Unit, AggType, Validity, Verification, ResultTime, DataCapture, FkObservationLog`. Keep rows with `Validity > 0`.
- `Start` is **UTC+1 (fixed, no DST)**: subtract 1 h to get UTC. This was confirmed by a lag scan, where the best correlation was at −1 h.
- `dataset: 1` (up-to-date, unverified stream) returned 2026 data only (2026-01-01 to now, about 6,400 valid hours). `dataset: 2` (verified) returned nothing for 2023–24 with the `cities` filter. Try again without `cities`, and filter sampling points `SPO-BG00*` yourself. Also try the PM10 pollutant: Sofia has about 5–6 PM10 stations.
- Station coordinates and type (traffic/background) must come from EEA station metadata. **Do not guess coordinates.**
- Check the EEA data licence and add attribution. Unlike WAQI, EEA data is generally reusable, but verify.

Results of the first check (BG0050A Hipodruma vs the Sensor.Community **city median**, about 1,990 overlapping hours):
- hourly r = 0.71, daily r = 0.84
- the sensors read **0.4–0.6× the reference**
- the ratio rises with humidity: 0.37 (RH ≤ 60) → 0.54 → 0.66 → 0.68 → 0.71 (RH > 95)
- the ratio is about 0.17 when the reference is above 50, but the sample is tiny: only 5 such hours at Hipodruma
- **BG0070A looks unreliable** (r = 0.31, median 15 with p99 21, a flat distribution). Exclude it unless you find out why.

This was a city-median comparison, not co-location. Redo it properly with sensors near the station.

Implication: the model learns sensor-scale values, and the health anchors in `config.AIR_PM` (WHO 5/15, EU 25) are applied to values that are roughly half the real concentration.

### 1.5 The main winter source is missing from the model
- NIMH (2020, cited by Breathe Cities) attributes **42 % of Sofia's PM emissions to solid-fuel heating**. Sofia Municipality (2024, cited by IEEP 2026) attributes **80 % of winter PM exceedances** to about 32,000 solid-fuel households. Toplofikatsia district heating covers about 70 % of households.
- Since 2025 a low-emission heating zone bans wood and coal heating in parts of nine districts.
- The model's only street features are traffic and terrain. Canopy, NDVI, surface heat and construction were tested and correctly rejected.

---

## 2. Target architecture

Every screen must read from this single chain, in **calibrated µg/m³** with uncertainty:

```
Stage 0  Sensor calibration
         raw Sensor.Community PM2.5 → reference scale
         global part:  log(ref) ~ log(raw) + RH terms + T + sensor type (+ season if it earns its place)
         sensor part:  per-sensor fixed offset learned from history, shrunk toward 0 (random effect)
         output:       calibrated PM2.5 per sensor-hour + an error variance per sensor

Stage 1  City level (keep the existing design: gradient-boosted ratio to CAMS)
         target:  calibrated city level (calibrated sensor median, anchored to reference hours where available)
         outputs: quantiles p10 / p50 / p90 (HistGradientBoostingRegressor(loss="quantile"))
         new candidate features: heating-degree hours, heating-season × night, live PM10/PM2.5 ratio

Stage 2  Spatial field (replaces IDW)
         regression kriging / GP in log space
         trend:    traffic, terrain, solid-fuel heating density (Phase 4), (ELAPSE prior if obtained)
         residual: calibrated sensors minus trend, with a per-sensor nugget = its error variance
         output:   p50 and SD for every street segment

Stage 3  Outputs
         banner  = P(district PM2.5 > 25 for ≥ 3 consecutive hours within the window), plus the expected value,
                   plus a plain level: "Fine / Sensitive groups / Everyone"
         chart   = model p50 with a p10–p90 band (CAMS shown only as a faint reference line, labelled)
         map     = p50; streets where SD exceeds the local street-to-street spread are drawn faded / "uncertain"
         router  = offer "cleanest" only for hours where the route difference exceeds combined uncertainty
         stagnation score = a model input and a chart overlay, no longer the alert trigger
```

Performance constraint: the app precomputes 48 h × about 88k edges (`WINDOW_HOURS = 48`). Within one sensor snapshot the sensor locations are fixed, so **precompute the kriging weight matrix (edges × sensors, sparse k-nearest) and the per-edge variance once per snapshot**. Each hour is then a sparse matrix–vector product. The full window must not get noticeably slower than today: measure it.

---

## 3. Phases

### Phase 0: make the existing numbers agree (small, do first)
1. Banner: compute the alert from the **learned model's city-level PM2.5** (`CaiModel` B / level per hour), not from `label_for(score)`. Keep the stagnation score as supporting text ("air is trapped: score 72").
2. Put the expected PM2.5 in the message, e.g. "Tomorrow night 22:00–06:00: PM2.5 around 38 µg/m³. Sensitive groups should limit time outside."
3. Thresholds: 15 = WHO 24 h guideline (watch), 25 = EU 24 h limit (warning). Put them in config.
4. Chart: plot the model level. Show CAMS as a secondary, labelled line, or drop it.
5. Advice (`/api/advice`) and dashboard use the same alert object.
6. Fix the README contradiction (slider 48 h vs "24 hourly slots").
7. Tests: update `backend/tests/test_api.py` and the related tests. Add a test that a clean forecast with a high stagnation score does **not** produce a red banner.

Note: until Phase 1 lands, the model level is still on the sensor scale (about 0.5× reality). Say so in the banner footnote, or keep Phase 0 unmerged until Phase 1 is done. Pick one and state which.

### Phase 1: reference data and calibration
1. `backend/data_prep/reference_eea.py` plus a Makefile target `make reference`. It downloads PM2.5 and PM10 hourly data for all Sofia sampling points, from as far back as the API gives (try both datasets), and stores `data/history/reference.csv` (`station, pollutant, lat, lon, station_type, hour_utc, value`) together with the station metadata.
2. Co-location table: sensor-hours within R of each reference station (start at 1.5 km and report sensitivity for 0.5 / 1 / 2 km), joined to reference hours and to `weather.csv` RH and temperature. Sensor type comes from the history downloader. If the CSV lacks it, extend `history.py` to keep it.
3. Fit and compare calibration models with **time-blocked CV (by week)**, never random rows:
   - a. ratio only
   - b. linear in log with an RH term (Barkjohn-style)
   - c. b + temperature + sensor type
   - d. small GBM
   Pick the simplest model within noise of the best.
4. PM10 cross-check: the same procedure against the PM10 stations. Does the PM2.5 calibration's RH behaviour look the same?
5. Re-run the WAQI vs sensor PM10 comparison (`sensor_check` in `main.py`) with calibrated values.
6. Apply calibration in `build_sensors` (live) and in `train.py` (history). Store the calibration in its own joblib file with a format version. The app must still run if it is missing (fall back to raw and label the data as uncalibrated).
7. Retrain the PM model on calibrated targets. Bump `FORMAT` in `pm_model.py`.
8. **Acceptance (proposed):**
   - On held-out weeks at Hipodruma, the calibrated city median has a median ratio to the reference of 0.85–1.15 in every RH bin.
   - MAE is at least 40 % lower than raw.
   - Report the >25 and >50 bins separately with counts.
   - If the bar is not reachable, report why. Do not loosen it silently.
9. Expected visible effect: the map gets about 2× more polluted on the same day. Document this in `PM_MODEL.md` and the README: it is a correction, not a regression.

### Phase 2: sensor offsets and kriging instead of IDW
1. Per-sensor offset: a random effect estimated from history, the log deviation of the calibrated sensor from the calibrated city median after the trend. Shrink it toward 0 by the number of hours (empirical Bayes). New sensors with no history get offset 0 and a large nugget.
2. Decide and document **what an offset means**: calibration error or real local pollution. You cannot fully separate the two. The rule: remove the offset when using a sensor to estimate *other* locations. At the sensor's own location, report both values.
3. Fit an empirical variogram of the hourly log residuals pooled over history (by season if it differs). Fit a model (exponential or Matérn) and report range, sill and nugget. If the range is under about 300 m or the field is mostly nugget, say so: it means street-level interpolation from sensors carries little information.
4. Implement regression kriging with per-sensor nugget, either with `sklearn.gaussian_process` (per-sample `alpha`) or your own sparse local kriging (k nearest within a radius). Replace `idw_residual` in `score.py` and `validate.py`. Keep IDW behind a config flag for comparison.
5. Propagate uncertainty: per-edge SD = sqrt(level variance from Stage 1 quantiles + kriging variance), in log space.
6. **Acceptance (proposed):**
   - Leave-one-sensor-out over historical hours (not just today's snapshot): median error lower than current IDW.
   - Within-hour Spearman not lower than IDW.
   - **80 % intervals cover 75–85 %** of held-out readings, reported separately for heating season and summer.

### Phase 3: probabilistic level and alert
1. Quantile level model (p10/p50/p90) with the same CV scheme as `train.py`: sensor groups and week blocks, plus the future-days split.
2. Check calibration of the quantiles: the fraction of observations below p10 and above p90, overall and in episode hours.
3. Alert probability: P(PM2.5 > 25 for ≥ 3 consecutive hours) per district. Use the district's mean of the spatial field and a lognormal fitted to the quantiles, or sample.
4. **Back-test on every heating-season day in the history** (and on reference data where available). Compare against the current heuristic (score ≥ 60). Report:
   - hit rate (POD)
   - false-alarm ratio
   - missed episodes
   - median lead time
   Pick the probability threshold that maximises a stated score (e.g. Peirce skill), using the training period only.
5. **Acceptance (proposed):**
   - Episode-hour bias within ±5 µg/m³ in calibrated units.
   - POD at least equal to the heuristic's, at a lower false-alarm ratio.
   - Quantile coverage within ±5 points.

### Phase 4: residential heating feature
1. Data, in order of preference:
   - a. NSI 2021 census dwellings by heating fuel at the finest public level (check nsi.bg and the census portal)
   - b. Toplofikatsia district-heating network or service area (ask Sofia Municipality / Sofiaplan GIS)
   - c. low-emission heating zone polygons (Sofia Municipality, 2025)
   - d. an OSM proxy: density of `building=house|detached|semidetached_house` and buildings with `building:levels ≤ 2`, outside the district-heating area
   Build (d) first so there is something to test. Write down what you requested for (a)–(c) and from whom.
2. Bake it like the other rasters (see `backend/data_prep/*.py`, `features.py`), sampled with a sensible buffer (test 250 / 500 / 1000 m; heating smoke is a neighbourhood signal).
3. Test with the design in `backend/ml/construction_check.py`:
   - correlation with persistent sensor offsets
   - a **within-sensor contrast**: heating-season nights vs summer nights at the same sensor, against heating density
   - an interaction with stagnation in the spatial term
4. **Acceptance:**
   - It enters the model only if within-hour Spearman on held-out sensors in the heating season improves by ≥ 0.05 over traffic + terrain.
   - Otherwise it stays as context, and you document the negative result the same way the construction result is documented.

### Phase 5: decide street-level vs neighbourhood-level
1. Using the final model, report held-out within-hour Spearman and interval coverage **for heating-season hours with a city level above 15 and above 25**.
2. Decision rule (proposed):
   - If within-hour Spearman is under 0.3 in polluted heating-season hours, the default map becomes 250 m–1 km cells (reuse `backend/cai/areas.py`), with street colouring as an opt-in "indicative" layer.
   - The router shows "cleanest" only when the fastest and cleanest exposure differ by more than their combined uncertainty. Otherwise it says "All routes have about the same air right now."
3. Update the validation card (`frontend/src/components/ValidationCard.tsx`, `/api/validation`) with the new metrics, including reference-station agreement.

### Phase 6: documentation
- `docs/PM_MODEL.md`: add a section per phase with tables (model vs baselines on the same rows), what was tried and dropped, and limits.
- `README.md`: fix "stations report no PM2.5", "sensors read high in humid air" (they read low in Sofia, and the ratio rises with RH), the data table (EEA source + attribution), the "How it works" diagram, and the honest caveats.

---

## 4. Rules

- **No test-set tuning.** Every threshold, alpha, radius and probability cut is chosen on training folds. Report held-out numbers only.
- Always compare against baselines on the **same rows**: persistence, CAMS × bias, current model, current IDW.
- Time-blocked or sensor-grouped CV only. Never random row splits: hours and neighbouring sensors are strongly correlated.
- Keep the app keyless and runnable offline from fixtures. New data sources must have fixtures or a graceful fallback.
- WAQI data must not be archived (their terms). EEA data may be stored if the licence allows: check and attribute.
- No paid APIs. No new heavy dependencies without need (pykrige or GPyTorch only if sklearn/scipy are clearly insufficient; say why).
- Keep `C.USE_ML = False` and missing-file fallbacks working. Bump format versions when saved layouts change.
- `make test` must pass at the end of each phase. Add unit tests for calibration (direction of the RH effect, missing RH, missing calibration file), kriging (exact at a noiseless sensor, reverts to trend far from sensors, variance grows with distance) and the alert probability (monotonic in level).
- Match the existing code style: compact, numpy-vectorised, docstrings that explain *why*, all tunables in `backend/cai/config.py` or the module header.
- If a phase's acceptance bar fails, stop that phase, document the result, and continue with the next phase only if it does not depend on the failed one.

---

## 5. What to report back at the end

1. Per phase: what changed (files), the key numbers in a table against baselines, whether the acceptance bar was met, and any bar you could not meet and why.
2. The data you could not obtain and who to ask (census heating by district, district-heating map, ELAPSE surfaces, NIMH station forecasts).
3. The final recommendation: street-level, neighbourhood-level, or both, with the evidence.
4. Anything in this prompt that turned out to be wrong.

## 6. Out of scope (unless needed for the above)

UI polish from the external evaluation (axis overlap, date format, mobile header, tab names, plan codes, deep links, address search, notifications, municipality sample data). Leave them for a separate task, except where Phases 0, 3 and 5 change the banner, chart, map and router behaviour.

## 7. References

- Schneider et al. 2017, *Environment International* 106:234–247, near-real-time urban mapping from low-cost sensors and a model (Oslo). Background: [CITI-SENSE D6.3](https://co-citi-sense.wp2.nilu.no/wp-content/uploads/sites/15/CITI-SENSE_D6.3.pdf)
- INERIS SESAM, universal kriging with external drift and a sensor error term: [FAIRMODE 2022](https://fairmode.jrc.ec.europa.eu/document/fairmode/event/presentation/Oslo-technical-2022/20221019_CT6_Data_fusion_with_sensor_data_AliciaGressent_INERIS.pdf)
- Lepioufle et al. 2024, distance-attention fusion vs kriging: [EDS](https://resolve.cambridge.org/core/services/aop-cambridge-core/content/view/14F5F461955D8FCBBAF0EEA3D29E45D2/S2634460224000189a.pdf/data-fusion-of-sparse-heterogeneous-and-mobile-sensor-devices-using-adaptive-distance-attention.pdf)
- Shetty, Schneider et al. 2026, low-cost sensors in 1 km European PM2.5 ML maps (S-MESH): [NILU](https://nilu.no/?p=59494)
- Varga-Balogh et al. 2020, CAMS downscaling in Budapest: [Atmosphere](https://doaj.org/article/821f0d049296427088bd8b127855e575)
- INERIS ML post-processing of CAMS: [ACP 2023](https://acp.copernicus.org/articles/23/5317/2023/)
- Barkjohn et al. 2021, PurpleAir correction with RH: [AMT](https://amt.copernicus.org/articles/14/4617/2021/)
- Di Antonio et al. 2018, κ-Köhler RH correction: [Sensors](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6164928/)
- AI4EU calibrated Sensor.Community Sofia PM10, Jan–May 2022: [AI4Europe](https://www.ai4europe.eu/research/ai-catalog/sensor-community-ai-calibrated-data-sofia-jan-may-2022)
- Kraków RF vs kriging with low-cost sensors: [MGRSD 2025](https://reference-global.com/article/10.2478/mgrsd-2025-0026)
- Quantile models for exceedance probability: [arXiv 2003.11356](https://ar5iv.labs.arxiv.org/html/2003.11356) · NGBoost sensor calibration with exceedance maps: [PMC8173015](https://pmc.ncbi.nlm.nih.gov/articles/PMC8173015)
- de Hoogh et al. 2018, ELAPSE 100 m surfaces: [paper](https://utrecht-main-test.atmire.com/items/0626ab66-b183-4989-9457-41f1cf696338) (no public download found; ask the authors)
- EEA download service: [portal](https://aqportal.discomap.eea.europa.eu/download-data) · [airbase client](https://airbase.readthedocs.io/en/stable/api/airbase.html)
- Sofia heating: [IEEP 2026 case study](https://ieep.eu/wp-content/uploads/2026/04/Sofia-case-study-IEEP-2026.pdf) · [Breathe Cities / NIMH](https://www.eneffect.bg/en/projects/53/breathe-cities)
- NIMH 72 h PM10 early warning for Sofia: [Neykov & Neykova 2021](https://www.meteo.bg/meteo7/sites/storm.cfd.meteo.bg.meteo7/files/Neykov_Neykova_24.09.2021.pdf)
- Not useful at street scale, for context only: Microsoft Aurora (global air-pollution foundation model, about CAMS skill) and CAMS Europe 0.1° (already the regional input via Open-Meteo).
