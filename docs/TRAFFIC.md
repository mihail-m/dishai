# Traffic: observed data only

**The PM2.5 estimate has no traffic term.** Earlier versions modelled traffic from OSM road-class weights, a hand-set rush-hour
curve and a traffic gain. Those were assumptions, they did not improve predictions at held-out sensors, and they were removed
(`config.W_CLASS` survives only for the data-prep baker; nothing reads it at serving time). Real traffic is shown as measured data.

## What is measured (all optional, keys in `.env`)
| Source | Key | Shown as |
|---|---|---|
| Google Routes, `TRAFFIC_AWARE_OPTIMAL` | `GOOGLE_ROUTES_SERVER_KEY` | typical travel time by hour of week (future `departureTime`) and live travel time, per corridor |
| TomTom Flow Segment Data | `TOMTOM_API_KEY` | current vs free-flow travel time at 3 points per corridor |
| TomTom Incident Details | `TOMTOM_API_KEY` | jams, closures, roadworks on the map |
| Google Air Quality, Pollen | `GOOGLE_ROUTES_SERVER_KEY` | independent PM2.5 / AQI / pollen card |

`backend/traffic.py` picks up to 12 arterial corridors (spokes out of the centre, chosen from our own street network) and asks Google
for the driving route; the street segments along the route belong to the corridor. Per corridor: index = travel time / quietest-hour
travel time (1.0 = free flow), live vs typical = live index / typical index for the hour (mean of Google and TomTom live readings).
The typical profile (24 weekday + 24 weekend departures per corridor) is saved as `data/traffic_profile.json` (derived indices only, 7 days).

In the app: corridors coloured lighter-to-heavier than usual and incident markers on the map; a "Real traffic" card; and in the street
popover an "Observed traffic" line for streets on a measured corridor ("no measurement, none assumed" elsewhere).

## Why it is not in the PM2.5 number
Turning a travel-time index into µg/m³ needs a coefficient, and a coefficient needs traffic history lined up with the sensor record.
We have none: Google only serves typical and live times, and TomTom Traffic Stats (licensed history) needs a partner contract. Any
number picked now would again be an assumption. Once history exists (Traffic Stats, or logged observations over several weeks) the
effect can be fitted and validated like terrain was; until then the map shows traffic without claiming an effect on PM2.5.

## Learned model
The saved model (`data/pm_model.joblib`) was trained with the old assumed-traffic feature `T`. At serving time `CaiModel._static`
replaces `T` with the value the model treats as typical (z = 0), so no street moves because of it. Retraining with `T` removed from
`STATIC_COLS` / `SPATIAL_COLS` (ml/train.py, ml/spatial_fit.py) makes that a no-op.

## Limits
- Travel times, not vehicle counts. Arterials only; streets in between have no measurement.
- Google content is held in memory (15 min) except the derived hourly index. Check the Maps Platform terms before using this beyond a demo.
- Daily request caps: `TRAFFIC_GOOGLE_DAILY_MAX` (1500), `TRAFFIC_TOMTOM_DAILY_MAX` (2300). Refresh is lazy (only when the app is used).
- TomTom's flow raster/vector tiles were tested and not used: their `traffic_level` did not match the segment API (correlation 0.04-0.35).

## Endpoints
`GET /api/traffic`, `GET /api/traffic/incidents`, `GET /api/google-air`, `GET /api/cai/edge` (`observed_traffic`),
`GET /api/traffic/tiles/{z}/{x}/{y}.png` (TomTom delay raster proxy, unvalidated, not shown in the UI).


## Other sources evaluated (October 2026)
| Source | Real time | History | Verdict |
|---|---|---|---|
| **BGTOLL open data** (bgtoll.bg/en/open-data, CC BY 4.0) | 15-minute counts, speeds and vehicle classes per control point, but published to Dropbox in daily batches (latest file was the previous day); no live endpoint is documented | 15-minute archive 2026-03-05 onward (976 MB zip, downloaded); per-vehicle crossings 2020-2025 as one 228 GB zip stream (no ranges, not fetched) | **Used** for the measured hour-of-week volume profile (`backend/data_prep/bgtoll.py` -> `data/bgtoll_profile.json`). Control points are on the national network around Sofia (12 on the ring road and approaches), not city streets. |
| TomTom Traffic Index (tomtom.com/traffic-index) | no | yearly city aggregates, charts only | Not usable: no download or API, all rights reserved. The licensed route to the same data is TomTom MOVE / Traffic Stats (30-day trial). |
| MDPI Appl. Sci. 13(8) 4678 (Brezov and Burov, 2023) | no | modelled street-level traffic for Sofia (baseline 2018) | A *model*, not measurements; no data availability statement found. Worth an email to the authors (Sofia University) for the street-level volumes. |
| xmap.ai Bulgaria road traffic data | claims real time | not stated | Commercial catalogue (road-segment speeds, FRC); sample by form, no price, licence or source stated. Ask for a sample if street-level history is needed. |
| trafficindex.org | no | 2017-2025 charts, paywalled | Not usable: scraped from Google Maps, subscription, rights reserved. |

### Does measured traffic improve the PM2.5 estimate? (tested, no)
Hourly ring-road volume (12 BGTOLL points) against hourly PM2.5 at the EEA stations Hipodruma and BG0050A, 5 March to 16 October 2026 (about
5,000 hours each). Baseline: log PM2.5 on log CAMS, boundary-layer height, wind, temperature, humidity, rain, cloud and hour-of-day. Adding log traffic:
week-blocked cross-validated RMSE changed by +0.10 % and -0.05 % (no improvement); the coefficient is +0.04 to +0.07 (a doubling of traffic is +3 to +5 % PM2.5,
90 % interval touching zero at one station). Weekend alone was worse. Limits: summer-heavy (no heating season), ring-road counts are not street-level, and
hour-of-day already carries most of the daily traffic pattern. So it is **not** a model input. The measured profile also shows the old hand-set curve was wrong at night:
traffic at 02:00-04:00 is about 6-9 % of the weekday peak (the removed curve said 40 %), peaks are at 08:00 and 17:00, and weekend volume is 0.77 of weekday (old guess 0.75).
