# Inversion Watch v2: a street-level Clean Air Index, clean-air routes, and a clean-air network map

Written for: the hackathon team and the Claude Code session that will extend the POC from [PLAN.md](PLAN.md).

## 1. The idea in one paragraph

In v1 the "score" is a single city-wide stagnation number. In v2 it becomes a street-level **Clean Air Index (CAI)**. Traffic, greenery, weather and heat islands are not extra map layers. They are inputs to one score, computed for every street segment and every forecast hour. Everything else is built on that score:

- the map shows CAI, not four separate layers
- the commute planner picks the path with the lowest exposure
- a "clean-air network" visualisation shows which streets form Sofia's clean corridors, and where everyone is forced through dirty air

## 2. Research findings (verified 2026-10-17 unless marked)

### Decision that shapes the architecture: Google cannot route on our score

| Fact | Consequence |
|---|---|
| Google Routes API has no custom edge costs. It returns its own fastest and a few alternative routes. | We cannot make Google "prefer clean streets". |
| `computeAlternativeRoutes` works only for requests **without intermediate waypoints** (doc quote). The docs I read show only a `DRIVE` example, so walking and cycling alternatives are **unconfirmed**. | Don't build the planner on Google's alternatives. |
| Up to 10 intermediate waypoints stay in the cheapest billing tier (Essentials). `TRAFFIC_AWARE` modifiers move a request to the Pro tier. Two-wheeler routing is Enterprise. | Keep waypoint counts at 8 or fewer. I could not get the free caps or per-1,000 prices; check the pricing page and set a quota cap. |

So the design is **our own router on an OSM walking graph, with Google used where it is strong**:

1. Google Maps JS API for the basemap, place search and the familiar look.
2. Google Routes API `WALK` as the **baseline**: "Google's route". We score that polyline with our CAI so the demo can say "Google's fastest route: exposure 100. Our clean route: exposure 72, +9% time."
3. Our clean path is simplified to 8 or fewer waypoints and sent back to Routes API as `intermediates`. Google then returns a polyline and ETA that a user can trust, and the route can open in Google Maps. Fallback if the call fails: draw our own polyline.

### Data availability

| Input | Source | Status |
|---|---|---|
| Weather | Open-Meteo forecast (already in v1). Add `apparent_temperature`, `shortwave_radiation`, `relative_humidity_2m` | Works, no key |
| Background PM | Open-Meteo Air Quality (CAMS, about 11 km) | Works, no key |
| Local PM | Sensor.Community, about 1,000 records in the Sofia bbox, used to calibrate and validate | Works, no key |
| Road network and road class | OSM via `osmnx` | Overpass answered this session (it was overloaded earlier). Cache the graph to disk. |
| Greenery (best) | Sentinel-2 L2A from Microsoft Planetary Computer STAC: NDVI = (B08 − B04)/(B08 + B04) | Verified: `sentinel-2-l2a` returned 10 m scenes for tiles T34TFN and T34TGN from Sept 2026 with under 10% cloud. Needs the `planetary-computer` package for signed URLs. |
| Greenery (fast fallback) | OSM parks, forest, `natural=tree`, `tree_row` | Always works |
| Heat islands | Landsat 8/9 Collection 2 L2, band `lwir11` (30 m) via the same STAC | Verified: `landsat-c2-l2` returned scenes (Sept 2026) with a `lwir11` asset. Read scale and offset from the asset's raster metadata; don't hardcode. Use a summer clear-sky median. |
| Traffic (always works) | OSM road class × time-of-day curve | Proxy, no feed needed |
| Traffic (optional live upgrade) | Routes API `DRIVE` + `TRAFFIC_AWARE` on about 10 arterial corridors to get a congestion ratio | **Not verified**, Pro billing tier. Treat as a stretch goal. |
| Cross-check | Google Air Quality API. Bulgaria is covered; default index for BG is `bgr_niggg`; `eaqi` is not listed for BG | Display or validation only. I did **not** check whether its terms allow caching derived data, so keep it out of the score. |

District boundaries are no longer needed. The score lives on street segments and a 250 m grid, which removes the v1 risk (Overpass admin_level 9 returned 121 neighbourhoods, not the 24 districts).

### What to expect from clean-air routing (from the literature)

| Study | Exposure reduction | Longer route |
|---|---|---|
| DeepPlume, Paris | 32% | 11% |
| CAR, Taiwan | 17.1% avg | 2.4% avg |
| Barcelona demo (AAMAS 2025) | 7.8% (NO2) | 4.5% |

Plan on **roughly 10–30% less modelled exposure for a detour under about 10%**. Show whatever your data gives and don't promise more.

## 3. The Clean Air Index

For every street segment `e` and hour `t`:

```
# 1. Regional level: CAMS forecast, bias-corrected by live sensors
B(t)      = CAMS_pm25(t) * bias(t)         # bias = median(sensor PM2.5) / CAMS now, clamped 0.5..2
R(x,t)    = IDW residual of sensors vs B     # spatial correction, k=4, 1.5 km radius, shrink toward 0

# 2. Local traffic increment (relative µg/m³, tunable)
T(e)      = sum over drivable roads r within 150 m of  w_class[r] * exp(-dist(e,r)/40 m)
rush(t)   = 24-value hour curve (night 0.4, peaks 1.0 at 7–9 and 17–19, weekend x0.75)
# optional: rush(t) blended with live Google congestion ratio

# 3. Ventilation: the v1 stagnation score amplifies local sources
A(t)      = 1 + 1.0 * S(t) / 100             # S = v1 stagnation score, 0..100

# 4. Greenery shield (capped: evidence on vegetation and PM is mixed)
G(e)      = clamp(0.5*tree_canopy_frac_50m + 0.5*ndvi_norm_50m, 0, 1)
shield    = 1 - 0.30 * G(e)

# 5. Modelled PM2.5 on the segment
PM(e,t)   = B(t) + R(e,t) + T(e) * rush(t) * A(t) * shield

# 6. Heat burden (summer only)
H(e)      = clamp((LST(e) - median(LST city)) / 8 K, 0, 1)
gate(t)   = clamp((apparent_temp(t) - 26) / 8, 0, 1) * daylight(t)
g(t)      = 0.5 * gate(t)                    # at most half the cost can come from heat

# 7. Outputs
AirNorm   = clamp(PM / 60, 0, 1)
cost_burden(e,t) = (1 - g) * AirNorm + g * H(e)
CAI(e,t)  = round(100 * (1 - cost_burden))   # 100 = cleanest and coolest
```

Starting values for `w_class` (relative, tunable, **assumptions rather than measurements**):

| Road class | Weight |
|---|---|
| motorway, trunk | 12 |
| primary | 9 |
| secondary | 6 |
| tertiary | 3.5 |
| residential | 1.2 |
| service | 1 |
| living_street, pedestrian, footway, path, cycleway | 0.3 |

Rules of thumb:
- In winter `g≈0`, so CAI is pure air quality. In a summer afternoon, hot unshaded segments also cost.
- Heat islands only matter when it is hot. Don't punish a warm surface in January.
- The 0.30 greenery cap and all weights live in `backend/cai_config.py`.

**Credibility check (build this):** leave-one-out validation on sensors. For each outdoor sensor, predict PM2.5 at its location from the model without that sensor, then report the Spearman rank correlation and the median error. Show the number on a slide. If it's weak, say so; a measured weak model is better than an unmeasured strong one.

## 4. Clean-air routing

Graph: `osmnx` walking network for the Sofia bbox `(42.60, 23.15)–(42.80, 23.55)`. Start with the central area if the full graph is slow. Cache as GraphML or a pickle.

```
time_e      = length_e / walk_speed (4.8 km/h); steps ×1.5; avoid motorway/trunk
dose_e      = PM(e,t) * time_e                 # exposure in µg·min/m³
cost_e(λ)   = time_e * (1 + λ * cost_burden(e,t) * k)
```

- `λ` is the slider "Faster ↔ Cleaner", 0 to 3. Compute **three routes**: fastest (λ=0), balanced (λ=1), cleanest (λ=3). Cap each at +30% time over the fastest.
- Report time, distance, mean modelled PM2.5, total dose and % change in dose vs fastest.
- Cycling: same graph with the bike network and a higher exposure factor (breathing rate); treat it as a stretch goal.
- Google baseline: call Routes `WALK` for the same OD pair, decode the polyline, snap it to graph edges (nearest edge within 25 m), and compute its time and dose with the same formulas.
- Google-snapped clean route: Douglas-Peucker the chosen path to at most 8 vertices, send them as `intermediates` to Routes `WALK`, return Google's polyline and ETA, and show "Open in Google Maps" using the same waypoints. On any error, fall back to our own polyline silently.

## 5. Visualisations

1. **CAI map (replaces v1 districts):** street segments coloured by CAI for the selected hour, with a time slider, sensor dots on top and a legend. One map, one score. A "Why is this red?" popover on click shows the decomposition: background, traffic, greenery, ventilation, heat.
2. **Route compare:** three coloured route lines plus Google's baseline dashed, with a small card per route: time, extra time %, dose, % cleaner.
3. **Clean-air network map (the wow slide):**
   - Sample about 400 random origin–destination pairs across the city.
   - For each, compute the fastest and the cleanest walk.
   - Count how often each street is used by cleanest routes and by fastest routes.
   - Draw segments with width = clean-route usage, colour = CAI. Add an animated flow layer (deck.gl `TripsLayer`) of pulses along the busiest clean corridors.
   - Mark **chokepoints**: segments used by many fastest routes with very low CAI (where everyone is forced through dirty air). These are exactly the places where a municipality should plant, calm traffic, or add a crossing.
4. **Municipality tab (extends v1):** top chokepoints ranked by `usage × (1 - CAI/100)`, mapped to the plan measures they would help: A.4.3 (blue-green arches and acupuncture), A.4.7 (street washing), C.1.1 (low-emission zones), A.4.4 (tree growth). This is the same "we connect the plan's measures" story, now at street level.

Map stack: Google Maps JS API as basemap with deck.gl on top via `@deck.gl/google-maps` `GoogleMapsOverlay`. **If the Google key is missing or quota is hit, switch to MapLibre + OSM tiles via env flag `MAP_PROVIDER=maplibre`.** Our routing and CAI never depend on Google.

## 6. Architecture changes vs v1

```
backend/
  cai/
    config.py        # weights, caps, gates (one place)
    graph.py         # osmnx load + cache, edge midpoints, KD-tree/STRtree
    features.py      # traffic term, greenery, heat sampling at edge midpoints
    score.py         # PM(e,t), cost_burden, CAI for a given hour (vectorised numpy)
    validate.py      # leave-one-out on sensors
    routing.py       # three routes, dose, Google baseline scoring
    network.py       # OD sampling, usage counts, chokepoints
  google_routes.py   # Routes API client (server-side key), polyline decode, waypoints
  data_prep/
    ndvi.py          # Sentinel-2 median NDVI -> GeoTIFF/npz
    lst.py           # Landsat lwir11 -> LST -> GeoTIFF/npz
    greenery_osm.py  # parks/trees fallback
  fixtures/          # saved responses so the demo works offline
frontend/
  MapView (Google or MapLibre) + deck.gl overlay, RoutePlanner, NetworkView, Municipality
```

New endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/cai?hour=&bbox=` | segment GeoJSON with CAI and decomposition |
| `GET /api/route?from=&to=&lambda=&hour=&mode=walk` | fastest, balanced, cleanest, plus Google baseline |
| `GET /api/network?hour=` | precomputed usage and chokepoints (cached; recomputed on demand) |
| `GET /api/validation` | the leave-one-out stats |

Keys, kept out of git:
- `GOOGLE_MAPS_BROWSER_KEY` (HTTP-referrer restricted, Maps JS and Places only)
- `GOOGLE_ROUTES_SERVER_KEY` (IP or API restricted, Routes API only)
- Set a daily quota cap on both.

## 7. Build order (about 10–12 hours on top of v1)

| Phase | Hours | Output |
|---|---|---|
| 0. Data bake | 2 | Graph cached, NDVI and LST rasters generated and committed (or OSM-greenery fallback), fixtures saved |
| 1. CAI engine | 2 | `score.py` + CAI map + validation number |
| 2. Router | 2 | Three routes, dose numbers, API endpoint |
| 3. Google | 1.5 | Maps JS basemap, Places search, Routes baseline and snapped route, fallback flag |
| 4. Network viz | 2 | OD sampling, usage and chokepoint layers, flow animation |
| 5. Integrate and polish | 2 | Municipality tab, v1 stagnation score feeds `A(t)`, README, pitch slides |

**Cut order if time runs out:** live-traffic upgrade → bike mode → Google-snapped waypoints → NDVI/LST rasters (use OSM greenery and skip heat) → network flow animation. **Never cut:** CAI map, three routes with dose numbers, the validation slide.

## 8. Risks and honest caveats

- The CAI is a **screening model with assumed weights**, not a dispersion model. Say "indicative" on screen.
- Greenery effect is capped at 30% because the evidence on vegetation reducing street-level PM is mixed. Its stronger benefit is cooling.
- Satellite imagery is a snapshot (summer); LST and NDVI are static layers. Don't present them as live.
- Sentinel-2 and Landsat tiles need a cloud-free median; Sofia is covered by two Sentinel tiles (T34TFN, T34TGN).
- The routes are as good as OSM pedestrian data. Expect some missing crossings or private paths.
- Google ToS and billing: restrict keys, cap quotas, and don't store Routes responses beyond the session cache.
- Privacy: no user location is stored. Origin and destination are processed per request.

---

## 9. Prompt for Claude Code

Run this in the existing POC folder (the project from PLAN.md). Paste everything between the lines.

---

Extend the existing **Inversion Watch** project (FastAPI backend, React + TypeScript + Tailwind frontend, MapLibre, Recharts) with a street-level **Clean Air Index (CAI)**, a clean-air route planner, and a clean-air network visualisation. Read `PLAN2.md` first; it is the spec, especially sections 3 to 6. Work in phases, run the app after each phase, and report anything that doesn't work instead of hiding it.

**Principles**
- Traffic, greenery, weather and heat are inputs to ONE score (the CAI), not separate map layers. The map shows CAI only, with a "Why is this red?" popover that shows the decomposition (background, traffic, greenery, ventilation, heat).
- All weights and caps live in `backend/cai/config.py`. The formula is in PLAN2.md section 3; implement it vectorised with numpy over edge midpoints.
- The existing v1 stagnation score `S(t)` feeds the ventilation amplifier `A(t)`.
- Our routing never depends on Google. Google is used for the basemap, place search, a baseline route, and an optional "snapped" route. Add `MAP_PROVIDER=google|maplibre` (default maplibre when no key is set).
- Keep the demo offline-safe: cache every upstream call, save fixtures, and fall back to them.

**Phase 0: data prep** (`backend/data_prep/`, run once, commit outputs under `data/`)
1. Download the `osmnx` walking graph for bbox (42.60, 23.15)–(42.80, 23.55). If it is too slow, start with a central bbox. Save to `data/graph.graphml`.
2. `ndvi.py`: via Microsoft Planetary Computer STAC (`pystac-client` + `planetary-computer`), collection `sentinel-2-l2a`, bbox above, June to September, cloud cover under 10%, bands B04 and B08, compute a median NDVI composite, save a GeoTIFF clipped to the bbox. Tiles T34TFN and T34TGN cover Sofia.
3. `lst.py`: collection `landsat-c2-l2`, asset `lwir11`, summer scenes with cloud cover under 15%, apply the scale and offset from the asset's raster metadata, convert to Celsius, median composite, GeoTIFF.
4. `greenery_osm.py`: fallback that rasterises parks, forest, `natural=tree` and `tree_row` into a 10 m canopy-fraction grid. Used if the satellite steps fail.
5. Save sample responses (forecast, air quality, sensors, a Google route) to `backend/fixtures/`.

**Phase 1: CAI engine** (`backend/cai/`)
1. `graph.py`: load and cache the graph; compute edge midpoints, lengths, road class, and a spatial index of drivable roads.
2. `features.py`: per-edge traffic term `T(e)` with the class weights and exp(-d/40 m) decay; 50 m buffered greenery fraction and NDVI; LST anomaly. Precompute once and cache as a `.npz`.
3. `score.py`: `cai_for_hour(t)` returning per-edge arrays: PM, CAI, and each contribution. Include the sensor bias correction and IDW residual with shrinkage.
4. `validate.py`: leave-one-out over outdoor sensors; return Spearman correlation and median absolute error; expose at `GET /api/validation`.
5. `GET /api/cai?hour=` returns segment GeoJSON (simplified) with CAI and the decomposition. Unit tests for the score function and the bias correction.
6. Frontend: replace the district shading with CAI-coloured street segments and the time slider; keep sensor dots; add the popover.

**Phase 2: router** (`backend/cai/routing.py`)
1. Edge cost `time_e * (1 + λ * cost_burden * k)`, walking at 4.8 km/h. Return fastest (λ=0), balanced (λ=1) and cleanest (λ=3), each capped at +30% time over fastest.
2. For each route return the polyline, time, distance, mean PM2.5, dose (PM × minutes) and % change in dose vs fastest.
3. `GET /api/route?from=lat,lng&to=lat,lng&lambda=&hour=`. Tests with fixed OD pairs.
4. Frontend `RoutePlanner`: origin/destination pickers (click on map or Places search), "Faster ↔ Cleaner" slider, route cards with the numbers.

**Phase 3: Google integration** (`backend/google_routes.py`)
1. Server-side Routes API client with `X-Goog-FieldMask` set to only the fields you need (polyline, duration, distance). Key from `GOOGLE_ROUTES_SERVER_KEY`. Never log or return the key.
2. Baseline: call Routes `WALK`, decode the polyline, snap to edges (nearest within 25 m), score with the same dose formula, and return it as `google_baseline`.
3. Snapped clean route: simplify the chosen path to at most 8 vertices (Douglas-Peucker), send as `intermediates` to Routes `WALK`, return Google's polyline and ETA. Do not request alternative routes with waypoints (unsupported). On any error return our own polyline and set `snapped=false`.
4. Frontend: Google Maps JS basemap with the deck.gl `GoogleMapsOverlay` when `MAP_PROVIDER=google` (browser key `GOOGLE_MAPS_BROWSER_KEY`), MapLibre otherwise. Add an "Open in Google Maps" link built from the waypoints.

**Phase 4: clean-air network visualisation** (`backend/cai/network.py`)
1. Sample 400 random OD pairs inside the bbox (seeded). For each, compute the fastest and cleanest route; count per-edge usage for each.
2. Output usage (clean and fast), CAI, and chokepoints: edges with high fast-route usage and low CAI, ranked by `usage * (1 - CAI/100)`. Cache the result per hour bucket.
3. `GET /api/network?hour=`.
4. Frontend `NetworkView`: line width = clean-route usage, colour = CAI, animated flow pulses along the top corridors (deck.gl `TripsLayer`), chokepoints highlighted with a ranked side list. Each chokepoint lists which municipal measures would help (A.4.3, A.4.7, C.1.1, A.4.4).

**Phase 5: integrate**
- Update the municipality dashboard with the chokepoint list.
- Keep the Live and Winter-inversion scenario toggle working; in winter mode, heat gate is zero and ventilation is high.
- README: how to get keys, how to run each data-prep script, the CAI formula, the validation result, and a table mapping every screen to the plan measures.
- Verify in a browser: CAI map, routes between three test OD pairs (one central, one across the city, one suburban), the network view, both scenarios, and the offline fallback with the network disabled.

**Hard requirements**
- No API keys in git. Read keys from env; provide `.env.example`.
- Label everything "indicative"; show the validation number on screen.
- Do not cache or persist Google Routes responses beyond the in-memory session cache.
- Do not fail the demo if Google, Overpass or Planetary Computer is unreachable.

---
