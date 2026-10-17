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
| **Buildings under construction** (OSM `landuse`/`building=construction`, 1,842 areas, about 5 km²) | street ranking 0.03; sensors within 150 m of sites read slightly *lower*, not higher (correlation with the sensor offset −0.004, +0.044 after traffic and terrain). Shown as context in "Why is this red?" but not in the score. |
| Live-sensor PM10/PM2.5 ratio (dust vs smoke) | **not tested**: needs the PM10 history re-download, paused. The downloader already keeps PM10 for when it is re-run. |

The construction layer is coarse: OSM sites have no start or end dates and coverage is incomplete, so a real effect near active sites could be hidden. Sofia's building-permit data with dates would be the better test.

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
