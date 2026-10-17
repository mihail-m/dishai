# Inversion Watch: POC plan and build prompt

Written for: the hackathon team (and the Claude Code session that will build it).

## 1. What we are building

A forecast-to-action web app for Sofia's bad-air days. It predicts when pollution will be trapped by a temperature inversion, then shows each user the municipal measure that applies to them. Each measure is tied to a measure ID in the Sofia PDUEK (the municipal climate plan).

| Screen | Plan measure it enhances |
|---|---|
| Stagnation forecast map and timeline | A.3.4 (early warning), A.3.3 (self-cleaning potential of the air) |
| Citizen "what should I do?" cards | PUGM "green ticket", C.1.1 (low-emission zones), C.3.2 (energy-poor home renovation), A.5.4 (climate shelters) |
| Municipality dashboard | C.3.2 lead list by district, A.4.7 street-washing priority |

## 2. Research findings (verified 2026-10-17)

| Source | Status | Notes |
|---|---|---|
| Open-Meteo forecast API | Works, no key | `hourly=boundary_layer_height,wind_speed_10m,temperature_2m,temperature_850hPa,temperature_925hPa,wind_speed_925hPa,cloud_cover`. Wind is in km/h. The grid snapped Sofia to 42.73, 23.34 at 548 m. |
| Open-Meteo Air Quality API | Works, no key | `hourly=pm10,pm2_5,european_aqi`. CAMS model, coarse grid (about 11 km), so it gives one city-wide forecast, not per-district values. |
| Sensor.Community | Works, no key | `https://data.sensor.community/airrohr/v1/filter/box=42.80,23.15,42.60,23.55` returned 1,055 records. Value types `P1` = PM10 and `P2` = PM2.5. Includes indoor sensors (`location.indoor=1`) and non-PM sensors, so filter. SDS011 sensors read high in humid air. |
| District boundaries | Not downloaded | Overpass was at capacity and all mirrors failed during research. The municipality's GIS data is only being opened now (public consultation on terms in July 2026). Plan: fetch once at build time, commit the file, and never depend on it at runtime. |
| Low-emission zone boundaries | No official download found | The municipal map is at iSofMap (gis-sofia.bg). Legal basis is the ordinance under Council decision No. 931 of 15.12.2022. For the POC, hand-draw two rough polygons and label them "illustrative". |

Sources:
- [iSofMap low emission zones layer](https://gis-sofia.bg/en/post/new-in-isofmap-added-low-emission-zones-thematic-map)
- [OSM forum thread on GIS Sofia open data](https://community.openstreetmap.org/t/topic/145369)
- [BTA: Sofia opens part of its geodata](https://www.bta.bg/en/news/bulgaria/1084237-sofia-makes-part-of-city-geographic-data-public)

Data decisions:
1. Use Sensor.Community for spatial detail. Use the CAMS forecast only for the city-wide PM level.
2. The stagnation score is our own heuristic, not a validated model. Say so in the pitch. Inputs: boundary layer height (low is bad), wind at 10 m (calm is bad), the 925 hPa minus 2 m temperature difference (warmer aloft is an inversion), and night-time hours.
3. District outlines: one `scripts/fetch_districts.py` that tries Overpass and a mirror with backoff and writes `public/data/districts.geojson`. If that fails, fall back to a hand-made rough grid of 6 to 8 labelled zones so the demo never breaks.
4. Never call external APIs from the browser during the demo. A tiny backend caches everything and falls back to saved fixtures.

## 3. Stagnation score (v0)

For each forecast hour, compute a 0 to 100 score:

```
blh_term   = clamp((800 - blh) / 700, 0, 1)        # blh in metres; lower is worse
wind_term  = clamp((12 - wind10m_kmh) / 12, 0, 1)  # calm is worse
inv_term   = clamp((t925 - t2m + 2) / 8, 0, 1)     # warm air above cold surface
score = 100 * (0.40*blh_term + 0.30*wind_term + 0.30*inv_term)
```

Labels: under 35 good, 35 to 60 watch, 60 and above inversion warning. Weights and thresholds are tunable constants in one file. Add a "demo scenario" switch that replays a hand-built January inversion night so the demo shows the interesting case even in October.

District detail: scale the city-wide score by a simple factor per district (for example, basin floor versus foothills, using elevation). Overlay live sensor PM2.5 as dots. Be honest on screen that district shading is indicative.

## 4. Architecture (keep it small)

```
frontend: Vite + React + TypeScript + Tailwind + MapLibre GL (OSM raster tiles) + Recharts
backend:  FastAPI (Python 3.11+), httpx, in-memory TTL cache, fixtures/ for offline fallback
```

Endpoints:
- `GET /api/forecast`: hourly stagnation score, raw inputs, CAMS PM2.5 and PM10, 48 h
- `GET /api/sensors`: filtered, cleaned Sensor.Community readings (outdoor, valid PM2.5 under 500, last 30 min)
- `GET /api/districts`: GeoJSON with district name, elevation factor, and current score
- `GET /api/advice?profile=driver|wood_heater|vulnerable&district=...`: action cards
- `POST /api/leads`: C.3.2 self-reported interest (district, heating type only). Stores aggregates only, no personal data.
- `GET /api/dashboard`: lead counts by district, hotspot list, street-washing priority

## 5. Build order (about 6 to 8 hours)

1. **Hour 0 to 1:** scaffold, FastAPI with `/api/forecast` returning real data plus the score, fixture fallback.
2. **Hour 1 to 2:** `/api/sensors` with filtering. Map page with district polygons, shading and sensor dots.
3. **Hour 2 to 3:** 48 h timeline chart, warning banner, demo-scenario toggle.
4. **Hour 3 to 5:** citizen view with the three profiles and action cards. Zone lookup (illustrative polygons) and C.3.2 eligibility wizard that posts to `/api/leads`.
5. **Hour 5 to 6:** municipality dashboard. Hotspots, leads by district, street-washing list.
6. **Hour 6 to 8:** polish, offline-safe demo, pitch slide, README with plan measure mapping.

## 6. Demo script (3 minutes)

1. Open with the warning banner: "Inversion expected tonight 18:00 to 10:00" and the timeline.
2. Click a district. Show score, sensor dots, and the honest "indicative" label.
3. Switch profile to wood heater, show the zone check and the C.3.2 card, submit an interest.
4. Switch to the municipality dashboard, show the lead appear and the district counts.
5. Close with the mapping slide: every card cites a plan measure ID.

## 7. Risks and honest caveats

- The score is a proxy, not a validated model. Position it as early-warning triage.
- Low-cost sensors drift; show counts and a "citizen sensors" label.
- Zone polygons are illustrative until the municipality provides official geometry.
- C.3.2 grant amount (about 10.2k EUR per household) comes from the plan and may change under the national climate social plan. Show it as "up to, per the plan".
- Privacy: no addresses stored, no personal data, aggregate leads only.

---

## 8. Prompt for Claude Code

Copy everything between the lines into a fresh Claude Code session in an empty folder.

---

Build a hackathon POC called **Inversion Watch**: a smart-city app for Sofia, Bulgaria that forecasts temperature-inversion air-pollution episodes and turns them into actions for citizens and the municipality. Prioritise a working, good-looking demo over completeness. Work in small steps and run the app after each one.

**Stack:** FastAPI (Python 3.11+, httpx) backend; Vite + React + TypeScript + Tailwind frontend; MapLibre GL for the map (free OSM raster tiles, no API key); Recharts for charts. No database: in-memory state plus JSON fixtures.

**Data sources (all keyless, verified working):**
- Forecast: `https://api.open-meteo.com/v1/forecast?latitude=42.6977&longitude=23.3219&hourly=boundary_layer_height,wind_speed_10m,temperature_2m,temperature_925hPa,wind_speed_925hPa,cloud_cover&forecast_days=2&timezone=Europe%2FSofia` (wind in km/h)
- Air quality: `https://air-quality-api.open-meteo.com/v1/air-quality?latitude=42.6977&longitude=23.3219&hourly=pm10,pm2_5,european_aqi&forecast_days=2&timezone=Europe%2FSofia` (coarse grid; city-wide only)
- Sensors: `https://data.sensor.community/airrohr/v1/filter/box=42.80,23.15,42.60,23.55`. Each record has `sensordatavalues` with `value_type` `P1` (PM10) and `P2` (PM2.5, string values), and `location.latitude/longitude/indoor`. Drop indoor sensors, drop records without P2, drop P2 above 500, keep the latest per sensor id.

**Backend requirements:**
1. Cache every upstream call with a 10-minute TTL. If an upstream call fails, serve the last cached value, then fall back to `backend/fixtures/*.json` (save real responses there on first run). The demo must never break without internet.
2. Stagnation score per hour, constants in one file `backend/score.py`:
   `blh_term=clamp((800-blh)/700,0,1)`, `wind_term=clamp((12-wind10m_kmh)/12,0,1)`, `inv_term=clamp((t925-t2m+2)/8,0,1)`, `score=100*(0.40*blh_term+0.30*wind_term+0.30*inv_term)`. Labels: <35 good, 35 to 60 watch, >=60 warning. Add unit tests.
3. A `?scenario=winter_inversion` option on `/api/forecast` that returns a hand-built 48 h series with a strong night inversion (low boundary layer, calm wind, warm air aloft, PM2.5 rising past 80 µg/m³), so the demo shows the interesting case regardless of today's weather.
4. Endpoints: `GET /api/forecast`, `GET /api/sensors`, `GET /api/districts` (GeoJSON with per-district current score = city score times an elevation factor; basin-floor districts above 1.0, foothill districts below), `GET /api/advice?profile=driver|wood_heater|vulnerable&district=`, `POST /api/leads` (body: district, heating type; aggregate counts only, never store personal data), `GET /api/dashboard`.
5. `scripts/fetch_districts.py`: try to build `public/data/districts.geojson` from OSM admin_level 9 relations in the Sofia bounding box via Overpass with retry and exponential backoff and two mirrors. If it fails, write a hand-made rough 8-zone GeoJSON with names (Center, Lyulin, Mladost, Studentski, Nadezhda, Vitosha, Iskar, Slatina) and mark every feature `"approx": true`. Commit the output; the app must never depend on Overpass at runtime.
6. Also create `public/data/lez_illustrative.geojson` with two rough concentric polygons around the centre labelled "Low-emission zone (illustrative, not official)".

**Frontend requirements (three views, tab navigation, responsive, clean modern look, works in dark and light):**
1. **Map and forecast** (home): MapLibre map with district polygons shaded by score (green, amber, red), sensor dots coloured by PM2.5, and a time slider over the 48 hours. A prominent banner when any hour in the next 24 h is a warning ("Inversion expected tonight 18:00 to 10:00"). A Recharts timeline of score plus CAMS PM2.5. A scenario toggle (Live / Winter inversion demo). A small honest note: "Score is an indicative proxy; district shading is approximate; sensors are citizen-run."
2. **For me** (citizen): choose a profile (Driver, Heats with wood or coal, Vulnerable or older, with asthma) and a district. Show 2 to 4 action cards. Each card shows a small badge naming the municipal plan measure it implements, for example "PUGM green ticket", "C.1.1 low-emission zone", "C.3.2 home renovation", "A.5.4 climate shelter". The wood-heater profile shows a zone-lookup result and a short eligibility wizard (heating type, owns a single-family home, receives heating aid) that, on submit, posts to `/api/leads` and shows "up to about 10,000 EUR per household under the plan; final terms may change".
3. **Municipality dashboard**: top hotspot districts for the next 24 h (score times number of leads), leads by district bar chart, a ranked "wash these streets first" list that is derived from hotspot districts (use plausible placeholder street names clearly marked as sample data), and a count of active sensors.

**Quality bar:** README with run instructions (`make dev` or two commands), an architecture note, and a table mapping each screen to the plan measure it supports. Run the backend tests, start both servers, open the app in a browser, and check all three views visually in both Live and Winter-inversion modes before saying you are done. Report anything that does not work instead of hiding it.

**Out of scope:** auth, a real database, push notifications, mobile app, ML models.

---
