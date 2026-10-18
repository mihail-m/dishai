# dishai (Sofia)

From air-quality forecast to action. Predicts when pollution is trapped by a temperature inversion, scores **every
street segment** with a Clean Air Index (CAI), plans lower-exposure walking routes, shows where Sofia's clean
corridors and dirty chokepoints are, and ties each action to a measure in the Sofia climate plan (PDUEK).

> Everything here is **indicative**. The stagnation score and the CAI are screening heuristics with assumed weights,
> not validated dispersion models. Citizen sensors drift. Zone polygons are illustrative. See [Honest caveats](#honest-caveats).

## Run it

### Mobile demo

The separate **AirPath mobile app** lives in [`mobile/`](mobile/README.md). Double-click
**`Start Mobile.cmd`** on Windows or **`Start Mobile.command`** on macOS for the phone preview at
`http://127.0.0.1:5180`. Its sample forecast, route comparisons, and daily planning demo run without
the backend. A Capacitor Android project is included for running the app in Android Studio's emulator;
see the [mobile setup and recording guide](mobile/README.md). The existing web app still uses the launchers below.

### Double-click (Windows or macOS)

1. Keep the whole project folder together. If you downloaded a ZIP, extract it first.
2. On **Windows**, double-click **`Start DishAI.cmd`**.
   On **macOS** (Apple Silicon or Intel), double-click **`Start DishAI.command`**.
3. Wait for setup to finish. Your browser opens automatically when the app is ready.

No separate Python, Node.js, Docker, or API-key installation is needed. The first launch needs internet
and downloads Python 3.12, Node.js and dependencies; allow a few minutes. Tools and Python packages stay
inside the git-ignored `.local/` folder; frontend packages stay in `frontend/node_modules/`.
Later launches reuse these downloads and rebuild the frontend only when its inputs change.
Setup uses [uv](https://docs.astral.sh/uv/guides/install-python/) and the
[official Node.js distribution](https://nodejs.org/download/release/v22.23.3/), with archive checksum checks.

Keep the terminal window open while using the app. Press **Ctrl+C** to stop it. Reopening the launcher
while it is running opens the same app. It listens only on this computer, normally at `http://127.0.0.1:8000`;
if that port is busy it picks a free one and prints the address. Live data and map tiles need internet;
cached tools and built UI can be reused without downloading them again.

On macOS, a downloaded file may need **right-click → Open** the first time. Git clones preserve its
executable permission; if a ZIP extractor drops it, run `chmod +x "Start DishAI.command"` once in the project folder.
If setup fails, the window keeps the error visible: fix the connection/problem and double-click again.
Do not copy `.local/` or `frontend/node_modules/` between computers; let each computer set itself up.

For a setup check without opening a browser or fetching live data, run the launcher with `--smoke-test`.
Use `--no-browser` to run normally without opening a browser.

### Development commands (macOS/Linux shell)

```bash
make setup          # venv + pip + npm install (frontend/.npmrc points at the public npm registry)
make dev            # FastAPI on :8000 + Vite on :5173  ->  http://localhost:5173
make test           # backend tests
make start          # single process: build the UI and let FastAPI serve it on :8000
```

Without keys the app runs on **MapLibre + OSM tiles** and **our own router**. Optional Google keys: copy
`.env.example` to `.env` (git-ignored) and set `GOOGLE_MAPS_BROWSER_KEY` (Maps JS + Places, referrer-restricted) and
`GOOGLE_ROUTES_SERVER_KEY` (Routes API only, read by the backend, never exposed or logged). Set a daily quota cap on both.
`MAP_PROVIDER=google|maplibre|leaflet` picks the initial basemap (default `google` only if a browser key exists). **The Google paths were not
exercised against the real API** (no keys were available); the Routes client is covered by tests with a mocked transport.

Use **Live menu (top right) → Map style → Leaflet · OSM** for the full-colour OpenStreetMap presentation with Leaflet **1.9.4**.
The choice is remembered in that browser. Switch back to MapLibre at any time, or Google Maps when a browser key is configured.
Switching preserves the map position, selected route, hour and overlay controls. All providers share the same deck.gl overlays:
street CAI and selection, citizen/reference sensors, walking/cycling routes, area/heat grids, clean channels, chokepoints and animated flows.
Leaflet is connected through the [deck.gl Leaflet adapter](https://visgl.github.io/deck.gl-community/docs/modules/leaflet/api-reference/deck-overlay).
Optional Google Places search remains available with a browser key regardless of the selected map.

The forecast slider covers **48 hourly slots starting with the current Sofia hour** (`WINDOW_HOURS`; the demo scenarios are 24 h replays),
including tomorrow when needed. Street colours are loaded together in one compact forecast snapshot. Opening the route planner or network view
preloads every hour of the window for its current settings; progress appears below the slider. Once it says **all hours ready**,
scrubbing uses memory without fetching each hour. Changing endpoints, travel mode or route preference prepares
a new set; recent sets are reused when switching tabs. The heat layer is hourly like the air layer: one request per forecast hour, preloaded the same way.

**Refresh forecast** fetches weather, air quality and sensors again, bypasses the upstream cache, and rebuilds
the full window. The selected time is kept if it remains in the new window. A failed refresh keeps the previous
forecast; upstream outages are still labelled as stale/saved data. Automatic checks continue every five minutes.
Google route enrichment, when configured, runs only for the selected hour, not for every background preload.

Map interaction regression tests live in `frontend/tests`: from `frontend/`, run `npm ci`,
`npx playwright install chromium`, then `npm run test:maps`. They cover both Leaflet and MapLibre
with deterministic API responses and tiles, including street picking, route endpoints, heat/area tooltips,
animated flows, camera alignment, renderer switching, 24-hour playback without requests and refresh failure/races. To use an installed Chrome instead,
set `PLAYWRIGHT_CHANNEL=chrome` before running the tests.

## Screens

| Screen | What it shows | Plan measure it supports |
|---|---|---|
| **Explore** | One panel with a plain-language status card (verdict, PM2.5, next-24-hour outlook) and three layers. **Air quality**: neighbourhood squares (or indicative streets, "Why this colour?" breakdown), suggestions for where you are (open the windows, take a walk, run the air purifier), based on the air around your location when you share it (kept in the tab, never stored or sent). **Heat**: feels-like temperature of every area for the selected hour, 48 hours ahead (city forecast plus a learned local offset, [docs/HEAT.md](docs/HEAT.md)), coolest and warmest areas. **Alerts** at the top of the panel (PM2.5 episode, heat, dust, smoke) with causes and what to do, and notification settings ([docs/ALERTS.md](docs/ALERTS.md)). **Clean paths**: clean channels and busy streets with poor air with suggested measures, pedestrians or cyclists. The time bar at the bottom is the forecast chart: PM2.5 or air trapping on Air quality, feels-like temperature on Heat, and a slim control on Clean paths and Route. Official stations, validation and forecast charts behind accordions | A.3.4 early warning, A.3.3 self-cleaning potential of the air, A.4.3, A.4.4, A.4.7, C.1.1 |
| **Route** | Pick a start and an end: the cleanest walk against the fastest, time, average PM2.5, "Open in Google Maps". No settings. | PUGM green ticket (leave the car), A.3.4 |
| **My health** | One verdict for where you are (your location or district) with air score and local feels-like, a "what to do today" plan led by the active alerts, a 24-hour strip with the best window, optional profile and health choices (saved in the browser), alert notification settings, and the city programmes (zone lookup, C.3.2 eligibility wizard, anonymous lead) behind one line. Below that, the pollen and allergy section: Pollen forecast per plant (alder, birch, grass, mugwort, olive, ragweed): 4 daily peaks and an hourly line, the typical season calendar (10-day bins from model history, with today marked), UV index, ozone and mineral (Saharan) dust, Google plant detail when keyed, and an anonymous daily symptom check-in that is compared with the model's pollen level. Choosing the pollen you react to puts those plants first, drives the pollen cards in the advice above (with food cross-reaction and "season starts in N days" cards) | PUGM green ticket, C.1.1, C.3.2, A.5.4, A.3.4, A.3.3 |
| **City insights** (for the municipality and planners) | Hotspot districts, C.3.2 leads, street chokepoints (street level), street-washing list (**sample street names**) | C.3.2, A.4.7, A.3.4, A.4.3, A.4.4, C.1.1 |

The **Live menu** (top right) holds the **Live / Winter inversion / Summer heatwave / Birch season** switch, Refresh forecast and the map style. The demo scenarios (the last two drive the heat and pollen cards on *My health*) replays a hand-built January inversion (48 h, PM2.5 rising past 100 µg/m³), so the
interesting case shows even in October. In demo mode sensor values are simulated from live positions and the validation is disabled.

## How it works

```
Sensor.Community ──► calibration to the EEA reference scale (humidity, temperature, season)   [stage 0]
CAMS + Open-Meteo weather + live city median ──► city-level PM2.5, p10/p50/p90 (gradient boosting)  [stage 1]
sensor residuals, per-sensor offsets removed ──► regression kriging + uncertainty per street  [stage 2]
      terrain height (street trend) ─┘
                                     └──► banner (chance of an episode), chart, map, router, advice  [stage 3]
Sentinel-2 NDVI + tree inventory ──► greenery G(e);  Landsat LST ──► heat H(e)     (CAI only, summer, gated; NOT the heat map, see docs/HEAT.md)
Google Routes + TomTom ──► observed traffic (map and street popover only, NOT an input to PM)
```

```
level(t)  = learned city-level PM2.5, calibrated µg/m³: p50 with a conformal p10..p90 band       (backend/ml, backend/alert.py)
street    = expm1( log1p(level) + terrain_deviation(e) + rho(lead) * kriged_residual(e) )       (backend/cai/krige.py)
banner    = P(city level >= 25 µg/m³ for 3 h in a row, next 24 h): red from 0.7, amber ("possible") from 0.2
CAI       = anchors on the health values: PM2.5 5 -> 80, 15 (WHO 24 h) -> 60, 25 (EU 24 h limit) -> 40, 50 -> 15
g(t)      = 0.5 * clamp((apparent_temp-26)/8) * daylight        # heat only when hot and sunny; 0 in winter
cost_burden = (1-g) * air_burden(PM) + g * H        CAI = round(100 * (1 - cost_burden))     # 100 = cleanest, coolest
```

The stagnation score (how trapped the air is) is no longer the alert; it is an input to the level model, a chart overlay and the line "the air is
trapped" in the banner. The whole chain, with every test and negative result, is in [docs/PM_MODEL.md](docs/PM_MODEL.md). Rebuild with
`make reference calibrate train spatial backtest` (after `make history`).

All weights and caps are in [`backend/cai/config.py`](backend/cai/config.py); the stagnation score is in
[`backend/score.py`](backend/score.py).

* **Router** (`backend/cai/routing.py`): our own Dijkstra on an OSM walking graph (88k nodes, 238k directed edges, baked to
  `data/net.npz`). `cost = time * (1 + λ * cost_burden * 2)`, walking 4.8 km/h, steps ×1.5. Fastest (λ=0), balanced (1), cleanest (3), each
  capped at **+30 % time** by bisecting λ. Reports time, distance, mean PM2.5, **dose** (PM2.5 × minutes) and % change vs fastest.
  The routing burden is *not* clamped (see below), so "cleanest" still discriminates during a heavy episode.
  **"Cleanest" is only offered when it is really cleaner**: the difference in mean PM2.5 is compared with the street-level uncertainty
  (`backend/cai/route_confidence.py`); otherwise the panel says *All routes have about the same air right now*. On typical trips today's
  differences are under 1 % against an uncertainty of 4-10 %.
* **Google (optional)**: the baseline `WALK` route is snapped to our edges (≤25 m) and scored with the same formulas; our cleanest path
  is simplified (Douglas-Peucker, ≤8 waypoints) and sent back as `intermediates` so Google returns a trustworthy polyline and ETA.
  Any failure silently falls back to our own polyline. Nothing from Google is persisted (in-memory session cache only).
* **Network** (`backend/cai/network.py`, `areas.py`): 40 origins × 10 destinations (seed fixed), fastest vs cleanest trip, per-street usage. **Chokepoint** = street
  with many fastest-route users and low CAI, ranked by `usage × (1 − CAI/100)`. **Clean channel** = street used by >= 3 of the cleanest routes and above the city's 60th CAI
  percentile. **Areas** = length-weighted mean CAI (or surface temperature) of the streets in each 250 m cell; the strongest 10 % are drawn bolder.
* **Cycling**: a separate OSM cycling graph (`make bikegraph bake`), 15 km/h, steps removed. Dose uses an **assumed** 2× breathing rate (`BREATHING` in config). Google is not used for cycling.

## Validation

`GET /api/validation`, shown as "How far to trust this map". The numbers below are in reference-station µg/m³ (the sensors, calibrated); full tables in
[docs/PM_MODEL.md](docs/PM_MODEL.md).

| What | Result |
|---|---|
| Calibration vs the EEA station (weeks it never saw) | median ratio 0.93-1.02 in every humidity band; average error 3.3 µg/m³ vs 5.4 raw (-39 % for the whole city's median, -40 % near the station). **One station only**, 3 hours above 50 µg/m³. |
| City level, hidden sensors + future days | 4.6 µg/m³ average error vs 4.9 for "the city median stays" and 5.3 for the regional model x bias; on forecast episodes (CAMS >= 50) bias -3.2 µg/m³ vs +22.6 for the regional model x bias |
| Sensors predicted from their neighbours (polluted winter hours, city level > 15) | median error 7.7 µg/m³ vs 8.6 (old IDW method); ranking within an hour 0.35 vs 0.28; 80 % ranges hold 78 % |
| Street vs neighbourhood | a map of 250 m squares ranks held-out sensors as well as the street map (0.347 vs 0.349): **sensors support neighbourhoods, not streets** |
| Banner, winter 2025/26 back-test (episode = 3 h in a row >= 25) | red tier catches 67 % of episode windows (81 % of distinct episodes) with 12 % false alarms; the old stagnation-score rule catches 95 % with 71 % false alarms; amber tier (chance >= 20 %) catches 95 % with 55 % false alarms |

A snapshot check is still run live (each sensor predicted from the others, today): on a clean autumn evening nothing beats the terrain trend
(rank correlation 0.02, median error 2.6 vs 2.4 µg/m³ for the trend alone and 2.8 for the old IDW method), consistent with the summer rows in the full tables: sensor noise (±2 µg/m³) is as large as
the signal at clean-air levels. The assumed traffic term (OSM road-class weights, a hand-set rush curve and a gain) has been removed;
measured traffic is shown on the map but is not used in the PM2.5 number (docs/TRAFFIC.md).

## Data

| Input | Source | Notes |
|---|---|---|
| Forecast, CAMS PM | Open-Meteo (keyless) | cached 10 min, saved to `backend/fixtures/`, timestamps rebased to today when offline |
| Reference PM2.5 / PM10 | **European Environment Agency (EEA) e-reporting**, hourly, via the EEA download API (`make reference`) | Sofia stations from the EEA station table (coordinates and type are never guessed): PM2.5 at Hipodruma (BG0050A) and BG0070A (excluded, see docs), PM10 at five stations. Dataset 1 only, **from 2026-01-01**. Licence CC BY 4.0 ([EEA legal notice](https://www.eea.europa.eu/en/legal-notice)); credit: *European Environment Agency (EEA) e-reporting, reported by Bulgaria*. Stored under the git-ignored `data/history/`; only the fitted calibration is committed. |
| Reference stations (live) | Sofia Municipality via aqicn.org/WAQI API (`WAQI_TOKEN`, optional) | 6 live stations: PM10, NO2, O3, CO, **no PM2.5 on this feed** (the EEA stream above does have it). In-memory only (their terms forbid archiving), never in fixtures. Attribution shown on screen. |
| Sensors | Sensor.Community (keyless) | outdoor, valid PM2.5 < 500, last 30 min; 24 h means used for validation. **Calibrated** to the reference scale before use (`data/calibration.joblib`); the raw value is kept as `pm25_raw`. Without the file the raw values are used and labelled uncalibrated. |
| Sensors (extra) | Sofia's **Airthings pilot network** (Interreg Balkan-Mediterranean), via the anonymous endpoints of its public map, `platform.airthings-project.com` | ~19 outdoor stations in Sofia, hourly PM2.5/PM10 already calibrated by the project, so they skip our Sensor.Community calibration. Merged into `/api/sensors` with `source: "airthings"`; shown as blue-ringed dots. **The site states no licence**: data is held in memory only (never in fixtures), two requests per refresh with an identifying User-Agent, credit shown on screen. The endpoints are undocumented and may change; any failure just means no Airthings sensors. They have no per-sensor history yet, so the spatial model treats them as new sensors (offset 0, full offset uncertainty). Switch off with `AIRTHINGS_DISABLED=1`. Ask the project/municipality for a documented feed and permission before relying on it. |
| Walking graph, roads | OSM via osmnx | `make graph roads bake`; GraphML (112 MB) is git-ignored, `data/net.npz` (11 MB) is committed |
| NDVI | Sentinel-2 L2A via Planetary Computer | `make ndvi`, 40 m overview, median of ≤5 scenes per tile |
| LST | Landsat 8/9 C2 L2 `lwir11` | `make lst`, scale/offset read from asset metadata (0.00341802 / 149.0), QA-masked |
| Tree canopy | **Sofia tree inventory** (Sofiaplan GIS, 1.68 M trees with crown area), baked by `make trees` | Crown area per 10 m cell, sampled in a 50 m buffer. Correlates **0.70 with Sentinel-2 NDVI** at street level, vs 0.33 for the OSM approximation it replaces (kept as a fallback, `make canopy`). Only the aggregate raster is stored, not individual trees. Credit: Sofia Municipality / Sofiaplan (gis.sofiaplan.bg); the service states no licence. |
| Street names | OSM `name`, transliterated (official Streamlined System) | Cyrillic kept alongside. Footpaths take the nearest named street within 80 m ("Footpath near …"). Chokepoint markers sit on the street; only chokepoints inside Sofia's 24 districts are ranked. |
| Buildings (heating proxy) | OSM buildings via Overpass | `make heating` -> `data/lowrise.tif`, `data/buildings.tif`; tested and **not used** in the model (docs/PM_MODEL.md, Phase 4) |
| Districts | OSM `admin_level=6` | `make districts`; the plan said level 9, which is neighbourhoods (121 features) |
| Low-emission zones | hand-drawn | labelled "illustrative, not official" |

Sentinel-2 L2A assets on Planetary Computer carry **no** scale/offset metadata, so `ndvi.py` applies the ESA rule for processing baseline ≥ 04.00
(reflectance = (DN − 1000) / 10000). Satellite layers are summer snapshots, not live.

### Pollen and allergy data

| Input | Source | Notes |
|---|---|---|
| Pollen, UV, ozone, dust | CAMS European air quality forecast via Open-Meteo (keyless; `uv_index`, `dust`, `ozone`, `aerosol_optical_depth` and six pollen species, hourly, 4 days) | Modelled at ~11 km for the whole city, **not trap counts**. Pollen is only published during the European pollen season. Open-Meteo asks for attribution, and a key for commercial use. |
| Typical season | The same model's history for Sofia, 2022 to 2025 (`make pollen-calendar` -> `data/pollen_calendar.json`) | 36 ten-day bins per plant and a typical start and end (days at the CAMS season threshold, 5th to 95th percentile, median over years). A year counts for a plant only when the feed covers the months where it flowers. Olive does not reach the threshold in Sofia. |
| Plant detail | Google Pollen API (same server key as Routes; enable the Pollen API for it) | Optional. Adds hazel, ash, oak, pine and cottonwood, index per plant and cross-reaction notes. **Pollen is listed under Google's Pro and Enterprise pricing tiers: check the cost before enabling.** Held in memory for an hour. |
| Symptom check-ins | Users, anonymous | `data/symptoms.jsonl` (git-ignored): date, district, severity, the model's pollen level. No identifier, so a person can report more than once. A level needs 20 reports before a share is shown. |

No open measured pollen data for Sofia was found (the European Aeroallergen Network only shares with data contributors; see `docs/POLLEN_OUTREACH.md`). The UV bands are WHO's, the ozone anchors are the WHO peak-season guideline (100 µg/m³) and the EU information threshold (180 µg/m³); the pollen bands and the dust bands (50 and 100 µg/m³) are hand-set and indicative. **Pollen-aware routing is experimental**: for an allergic user the burden of a street grows by `POLLEN_GREEN_WEIGHT` (0.3, an assumption in `backend/cai/config.py`) × pollen level × greenery, and greenery is only a proxy for pollen sources.

## Reference stations and a sensor-quality finding

The README used to say the municipal stations report no PM2.5. That is true of the WAQI feed only: Bulgaria reports hourly PM2.5 (Hipodruma) and PM10
(five Sofia stations) to the EEA, and that stream is what the model is calibrated against.

**Sofia's citizen sensors read about half of the reference station** (raw median 4.0 vs 9.1 µg/m³ at Hipodruma, 13 sensors within 1.5 km, 1,991 hours of
2026), and the ratio **rises with humidity** from 0.36 (RH below 50 %) to 0.74 (above 90 %): they under-read, and under-read less in damp air. The same
humidity direction appears in PM10 too (coefficient -0.43 on 88 days vs -0.61 for PM2.5). This is why every sensor value in the app is calibrated. The
map is therefore about twice as polluted as before the calibration on the same day; that is a correction, not a regression.

With a `WAQI_TOKEN` the map also shows the municipal stations (ringed dots) and compares nearby citizen sensors with them. On 2026-10-17 around 21:30,
citizen-sensor PM10 read about **0.5x** the reference raw and **1.4x** after the PM10 calibration (88 days, five stations): the calibration over-reads in damp air and at low
concentrations; one hour, and the reference PM10 is converted from AQI, so treat it as a check, not a calibration.
aqicn's terms require attribution, forbid archiving their data, and ask non-profit public deployments to notify the WAQI team by email.
GATE's digital-twin station API (`citylab.gate-ai.eu`) was returning 502 when checked and exposes no station coordinates, so it is not used.

## API

`/api/forecast` `/api/sensors` `/api/districts` `/api/advice` (`toggles=`, and `species=` with the pollen_allergy toggle) `/api/leads` `/api/dashboard` (v1) ·
`/api/pollen` (per-plant forecast, UV / ozone / dust, season status) `/api/pollen/calendar` `POST /api/symptoms` `/api/symptoms/summary` ·
`/api/cai?hour=&bbox=` (segment GeoJSON + decomposition) `/api/cai/areas?metric=air|heat&mode=walk|bike&hour=&scale=absolute|relative` (area cells; heat is feels-like °C per hour) `/api/alerts` (what needs attention in the next 48 h) `/api/push/key` `/api/push/subscribe` `/api/push/unsubscribe` `/api/push/test` (web push) `/api/cai/geometry` (binary) `/api/cai/values` `/api/cai/edge` ·
`/api/route?from=lat,lng&to=lat,lng&lambda=&hour=&mode=walk|bike` (with a `confidence` block: is any alternative distinguishable from the fastest route?) `/api/network?hour=&mode=` `/api/validation` (calibration, spatial and back-test summaries under `trust`) `/api/reference`. All accept `scenario=winter_inversion|summer_heatwave|birch_season`.

`GET /api/forecast/window` returns the current display forecast (48 hours live, 24 in a demo scenario), original hour indices, sensor snapshot,
and base64-encoded uint8 street CAI arrays (one byte per segment per hour). `POST /api/forecast/refresh` rebuilds
it after expiring upstream data. Pass its `id` as `forecast_id` to edge, area, route and network requests to keep
all results on that same forecast even across refresh or midnight. Unknown/evicted IDs return 409 and require
refreshing. Four snapshots and 48 hourly networks are retained in memory. The legacy `/api/forecast` still
returns the full upstream series, used internally for daily validation and existing API clients.

## Honest caveats

* **Better greenery did not improve the validation.** After switching to the tree inventory the leave-one-out result is unchanged (ρ ≈ 0): the limit is sensor noise at ~5 µg/m³, not the greenery input.
* **Heat map = city forecast + a learned local offset** (about 220 temperature sensors, tested on sensors it never saw: rank correlation 0.3 overall, 0.4 on summer nights, 0.15 on sunny afternoons). Differences between places are a few degrees at most. The street score's own summer heat term still uses the Landsat surface temperature, which did not match measured air temperature (docs/HEAT.md). Cyclist exposure assumes a 2× breathing rate.
* **Alerts are indicative.** Dust, particles and smoke come straight from the Copernicus CAMS forecast (no correction layered on top), compared with what is usual for the month; fires come from NASA FIRMS (keyless regional files) and only say smoke may arrive, unless CAMS also shows it. The green ticket is entered by hand (`python -m backend.green_ticket add DATE --source URL`) because the municipality publishes no feed. Push notifications need VAPID keys (`python -m backend.push --gen-keys`); without them alerts appear while the page is open. Not exercised against a real push service: covered by recorded-data and fake-sender tests.

* **Street-level colours are indicative.** The sensors support how a neighbourhood differs from the city (a 250 m cell map is as good as the street map at ranking hidden sensors); they do not support one street versus the next. The map defaults to cells for that reason.
* **The warning is a probability, not a certainty.** The red banner misses a third of the episode windows in the test winter (6 of 32 distinct episodes; the amber tier catches all but one), and the model under-predicts the very heaviest hours. Level intervals drift too narrow on the most recent months.
* **Winter home heating, the main source, is not modelled.** The OSM low-rise proxy did not pass its test; census heating-fuel data or the district-heating map are needed.
* The CAI is a **screening model**. Traffic is not modelled (observed only, see docs/TRAFFIC.md); the remaining assumed constants are the health anchors and the heat gate. It is not validated (see above).
* Plan-spec `clamp(PM/60)` **saturates** whenever background PM2.5 exceeds 60 µg/m³ (every street gets CAI ≈ 0). The map keeps the spec but offers a
  *Relative* view (5th-95th percentile stretch); routing uses the unclamped burden. In a heavy inversion the street choice changes a cross-city
  walk's exposure by only about 5 % for ≈5 % more time; the background dominates.
* **Pollen is modelled, not measured.** The six-plant forecast and the season calendar are CAMS model output for all of Sofia at ~11 km; nothing here compares them with a local trap yet (the symptom check-in is the first step towards that). The pollen bands, dust bands and the pollen-aware route weight are approximations or assumptions.
* Routes are as good as OSM pedestrian data; expect missing crossings and private paths. No user location is stored.
* Sensors drift, and in Sofia they read **low** (about half of the reference station), less so in humid air; the calibration was fitted on **one** Sofia reference station and then checked against 52 EEA stations in 29 European cities (a fit that never saw Sofia is within 3 % of the Sofia one; calibrated values above 50 µg/m³ read 5 % low on average elsewhere), and the calibrated city value is typically off by about 37 %. Zone polygons are illustrative. The C.3.2 grant ("up to about 10,000 EUR") comes from the plan and may change.
* Street names in the "wash first" list are sample data. Chokepoint measures are suggestions mapped to the plan, not engineering assessments.
* `npm audit` reports transitive findings in deck.gl's loaders.gl (3D-tile / texture parsers this app does not use).
