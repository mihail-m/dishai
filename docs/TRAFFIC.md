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
