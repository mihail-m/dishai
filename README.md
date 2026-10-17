# Inversion Watch (Sofia)

From air-quality forecast to action. Predicts when pollution is trapped by a temperature inversion, scores **every
street segment** with a Clean Air Index (CAI), plans lower-exposure walking routes, shows where Sofia's clean
corridors and dirty chokepoints are, and ties each action to a measure in the Sofia climate plan (PDUEK).

> Everything here is **indicative**. The stagnation score and the CAI are screening heuristics with assumed weights,
> not validated dispersion models. Citizen sensors drift. Zone polygons are illustrative. See [Honest caveats](#honest-caveats).

## Run it

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

Use **Map style → Leaflet · OSM** above the map for the full-colour OpenStreetMap presentation with Leaflet **1.9.4**.
The choice is remembered in that browser. Switch back to MapLibre at any time, or Google Maps when a browser key is configured.
Switching preserves the map position, selected route, hour and overlay controls. All providers share the same deck.gl overlays:
street CAI and selection, citizen/reference sensors, walking/cycling routes, area/heat grids, clean channels, chokepoints and animated flows.
Leaflet is connected through the [deck.gl Leaflet adapter](https://visgl.github.io/deck.gl-community/docs/modules/leaflet/api-reference/deck-overlay).
Optional Google Places search remains available with a browser key regardless of the selected map.

Map interaction regression tests live in `frontend/tests`: from `frontend/`, run `npm ci`,
`npx playwright install chromium`, then `npm run test:maps`. They cover both Leaflet and MapLibre
with deterministic API responses and tiles, including street picking, route endpoints, heat/area tooltips,
animated flows, camera alignment and renderer switching. To use an installed Chrome instead,
set `PLAYWRIGHT_CHANNEL=chrome` before running the tests.

## Screens

| Screen | What it shows | Plan measure it supports |
|---|---|---|
| **Clean Air map** | Street-level CAI, 48 h slider, citizen sensors, "Why is this red?" breakdown, honest validation card, stagnation-score timeline | A.3.4 early warning, A.3.3 self-cleaning potential of the air |
| **Route planner** | Fastest / balanced / cleanest walk, Faster-Cleaner slider, time, exposure dose, Google baseline and snapped route when keyed, "Open in Google Maps" | PUGM green ticket (leave the car), A.3.4 |
| **Clean-air network** | 400 seeded random walks: clean-route usage (width) coloured by CAI, flow pulses along clean corridors, ranked **chokepoints** with suggested measures | A.4.3, A.4.4, A.4.7, C.1.1 |
| **For me** | Profile cards (driver / wood or coal heater / vulnerable), zone lookup, C.3.2 eligibility wizard (anonymous lead) | PUGM green ticket, C.1.1, C.3.2, A.5.4, A.3.4, A.3.3 |
| **Municipality** | Hotspot districts, C.3.2 leads, street chokepoints (street level), street-washing list (**sample street names**) | C.3.2, A.4.7, A.3.4, A.4.3, A.4.4, C.1.1 |

A **Live / Winter inversion demo** switch replays a hand-built January inversion (48 h, PM2.5 rising past 100 µg/m³), so the
interesting case shows even in October. In demo mode sensor values are simulated from live positions and the validation is disabled.

## How it works

```
Open-Meteo forecast + CAMS PM2.5 ──► stagnation score S(t) (v1, per hour, city-wide)
Sensor.Community ──► bias + IDW residual                                  \
OSM roads (class weights, exp(-d/40 m) out to 150 m) ──► traffic T(e)       > PM(e,t) ──► CAI(e,t) ──► map, router, network
Sentinel-2 NDVI + OSM canopy ──► greenery G(e);  Landsat LST ──► heat H(e) /
```

```
B(t)   = CAMS_pm25(t) * bias,  bias = median(sensors)/CAMS now, clamped 0.5..2
R(e,t) = IDW residual of sensors vs the model (k=4, 1.5 km, shrunk toward 0)
A(t)   = 1 + S(t)/100                       # the v1 stagnation score amplifies local sources
PM     = B + R + gain * T * rush(t) * A * (1 - 0.30 * G)
g(t)   = 0.5 * clamp((apparent_temp-26)/8) * daylight        # heat only when hot and sunny; 0 in winter
cost_burden = (1-g) * clamp(PM/60) + g * H        CAI = round(100 * (1 - cost_burden))     # 100 = cleanest, coolest
```

All weights and caps are in [`backend/cai/config.py`](backend/cai/config.py); the stagnation score is in
[`backend/score.py`](backend/score.py).

* **Router** (`backend/cai/routing.py`): our own Dijkstra on an OSM walking graph (88k nodes, 238k directed edges, baked to
  `data/net.npz`). `cost = time * (1 + λ * cost_burden * 2)`, walking 4.8 km/h, steps ×1.5. Fastest (λ=0), balanced (1), cleanest (3), each
  capped at **+30 % time** by bisecting λ. Reports time, distance, mean PM2.5, **dose** (PM2.5 × minutes) and % change vs fastest.
  The routing burden is *not* clamped (see below), so "cleanest" still discriminates during a heavy episode.
* **Google (optional)**: the baseline `WALK` route is snapped to our edges (≤25 m) and scored with the same formulas; our cleanest path
  is simplified (Douglas-Peucker, ≤8 waypoints) and sent back as `intermediates` so Google returns a trustworthy polyline and ETA.
  Any failure silently falls back to our own polyline. Nothing from Google is persisted (in-memory session cache only).
* **Network** (`backend/cai/network.py`): 40 origins × 10 destinations (seed fixed), fastest vs cleanest walk, per-street usage. Chokepoint =
  street with many fastest-route users and low CAI, ranked by `usage × (1 − CAI/100)`.

## Validation (leave-one-out against outdoor citizen sensors)

`GET /api/validation`, shown on the map screen. Measured 2026-10-17 around 13:00, a clean autumn day (sensors ≈ 6 µg/m³):

| Target | n | Spearman ρ | Median error, model | Median error, constant median |
|---|---|---|---|---|
| Latest reading | 261 | **0.08** | 2.34 µg/m³ | 2.00 µg/m³ |
| 24 h mean | 272 | **0.01** | 3.43 µg/m³ | 1.65 µg/m³ |

**The street-level pattern is not confirmed by the data on this day**: rank correlation is about zero and the model is no better than
guessing the median level. The signs of the individual terms are as expected (traffic +, heat +, greenery −) but tiny (|ρ| ≤ 0.13, n = 273).
Sensor noise (±2 µg/m³) is as large as the signal at these concentrations. A polluted winter day with real traffic contrast would be a fairer
test; none was available. Two parameters were set from this validation and both are one-line config changes: `TRAFFIC_GAIN` was halved to 0.5
(plan weights overshot absolute PM2.5: 24 h median error 3.3 vs 1.7), and `IDW_SHRINK_M` is 200 m (picked by leave-one-out; differences were small).

## Data

| Input | Source | Notes |
|---|---|---|
| Forecast, CAMS PM | Open-Meteo (keyless) | cached 10 min, saved to `backend/fixtures/`, timestamps rebased to today when offline |
| Reference stations | Sofia Municipality via aqicn.org/WAQI API (`WAQI_TOKEN`, optional) | 6 live stations: PM10, NO2, O3, CO, **no PM2.5**. In-memory only (their terms forbid archiving), never in fixtures. Attribution shown on screen. |
| Sensors | Sensor.Community (keyless) | outdoor, valid PM2.5 < 500, last 30 min; 24 h means used for validation |
| Walking graph, roads | OSM via osmnx | `make graph roads bake`; GraphML (112 MB) is git-ignored, `data/net.npz` (11 MB) is committed |
| NDVI | Sentinel-2 L2A via Planetary Computer | `make ndvi`, 40 m overview, median of ≤5 scenes per tile |
| LST | Landsat 8/9 C2 L2 `lwir11` | `make lst`, scale/offset read from asset metadata (0.00341802 / 149.0), QA-masked |
| Canopy | OSM parks, forest, trees | `make canopy`, 10 m grid, blended 50/50 with NDVI |
| Street names | OSM `name`, transliterated (official Streamlined System) | Cyrillic kept alongside. Footpaths take the nearest named street within 80 m ("Footpath near …"). Chokepoint markers sit on the street; only chokepoints inside Sofia's 24 districts are ranked. |
| Districts | OSM `admin_level=6` | `make districts`; the plan said level 9, which is neighbourhoods (121 features) |
| Low-emission zones | hand-drawn | labelled "illustrative, not official" |

Sentinel-2 L2A assets on Planetary Computer carry **no** scale/offset metadata, so `ndvi.py` applies the ESA rule for processing baseline ≥ 04.00
(reflectance = (DN − 1000) / 10000). Satellite layers are summer snapshots, not live.

## Reference stations and a sensor-quality finding

With a `WAQI_TOKEN` the map shows Sofia's municipal reference stations (ringed dots) and compares nearby citizen sensors with them.
On 2026-10-17 around 14:00, **citizen-sensor PM10 read about 0.4× the reference PM10** (0.34-0.67 at all five stations with PM10, 6-19
sensors within 1.5 km each; e.g. 5.6 vs 16 µg/m³ at Mladost). One hour, PM10 only, and the reference PM10 is converted from AQI, so
treat it as a warning, not a calibration: the stations do not report PM2.5, so they cannot calibrate the PM2.5 values the model uses.
aqicn's terms require attribution, forbid archiving their data, and ask non-profit public deployments to notify the WAQI team by email.
GATE's digital-twin station API (`citylab.gate-ai.eu`) was returning 502 when checked and exposes no station coordinates, so it is not used.

## API

`/api/forecast` `/api/sensors` `/api/districts` `/api/advice` `/api/leads` `/api/dashboard` (v1) ·
`/api/cai?hour=&bbox=` (segment GeoJSON + decomposition) `/api/cai/geometry` (binary) `/api/cai/values` `/api/cai/edge` ·
`/api/route?from=lat,lng&to=lat,lng&lambda=&hour=` `/api/network?hour=` `/api/validation` `/api/reference`. All accept `scenario=winter_inversion`.

## Honest caveats

* The CAI is a **screening model with assumed weights** (traffic class weights, rush curve, 30 % greenery cap). It is not validated (see above).
* Plan-spec `clamp(PM/60)` **saturates** whenever background PM2.5 exceeds 60 µg/m³ (every street gets CAI ≈ 0). The map keeps the spec but offers a
  *Relative* view (5th-95th percentile stretch); routing uses the unclamped burden. In a heavy inversion the street choice changes a cross-city
  walk's exposure by only about 5 % for ≈5 % more time; the background dominates.
* Routes are as good as OSM pedestrian data; expect missing crossings and private paths. No user location is stored.
* Sensors drift and read high in humid air. Zone polygons are illustrative. The C.3.2 grant ("up to about 10,000 EUR") comes from the plan and may change.
* Street names in the "wash first" list are sample data. Chokepoint measures are suggestions mapped to the plan, not engineering assessments.
* `npm audit` reports transitive findings in deck.gl's loaders.gl (3D-tile / texture parsers this app does not use).
