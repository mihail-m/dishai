"""Clean Air Index tunables. Everything here is an ASSUMPTION or a heuristic, not a measurement.

Formula: see PLAN2.md section 3.
    PM(e,t)  = B(t) + R(e,t) + T(e) * rush(t) * A(t) * shield(e)
    cost_burden = (1 - g) * (1 - air_score(PM)/100) + g * H(e)        CAI = round(100 * (1 - cost_burden))
"""

# --- bounding box (west, south, east, north) -----------------------------------------------------
BBOX = (23.15, 42.60, 23.55, 42.80)

# Use the learned PM2.5 model (data/pm_model.joblib) when it exists; False forces the hand-weighted physics formula.
USE_ML = True

# Hours ahead the forecast slider covers (and the app preloads). Every hour is computed on refresh and fetched per view
# (the network tab runs one 400-route computation per hour), so this trades reach against load time.
WINDOW_HOURS = 48

# How the live sensors correct the learned model street by street: "krige" = regression kriging in log space with per-sensor offsets
# removed and an uncertainty per street (backend/cai/krige.py, data/spatial.joblib); "idw" = the older inverse-distance mean of the
# raw residuals, kept for comparison and as the fallback when the spatial file is missing.
SPATIAL_METHOD = "krige"

# --- 1. regional level: CAMS bias-corrected by live sensors, then IDW residual ---------------------
BIAS_MIN, BIAS_MAX = 0.5, 2.0
CAMS_FLOOR = 2.0  # ug/m3; avoid dividing by a near-zero model value
IDW_K = 4
IDW_RADIUS_M = 1500.0
# A sensor this far away carries the same weight as the shrink-to-zero prior. Picked by leave-one-out among
# 100-1000 m: tighter shrinkage was marginally better (differences are small; all variants are weak).
IDW_SHRINK_M = 200.0
# What the IDW residual is measured against: "model" = sensor - (B + traffic term) (no double counting),
# "background" = sensor - B (literal PLAN2 wording). Chosen by leave-one-out validation.
RESIDUAL_AGAINST = "model"

# --- 2. traffic: NOT modelled ---------------------------------------------------------------------------------------
# The PM2.5 estimate has no traffic term. OSM road-class weights, a hand-set rush-hour curve and a traffic gain were assumptions that did
# not improve predictions at held-out sensors, so they were removed. Measured traffic (Google Routes, TomTom; backend/traffic.py) is shown
# as observed data. W_CLASS below only decides which OSM classes the baker samples as drivable roads (data prep); nothing reads it at
# serving time.
W_CLASS = {
    "motorway": 12.0, "motorway_link": 12.0, "trunk": 12.0, "trunk_link": 12.0,
    "primary": 9.0, "primary_link": 9.0,
    "secondary": 6.0, "secondary_link": 6.0,
    "tertiary": 3.5, "tertiary_link": 3.5,
    "unclassified": 2.0,
    "residential": 1.2,
    "service": 1.0,
    "living_street": 0.3, "pedestrian": 0.3, "footway": 0.3, "path": 0.3, "cycleway": 0.3,
}
DRIVABLE = {k for k, v in W_CLASS.items() if v >= 1.0}
TRAFFIC_SPACING_M = 5.0  # baking only: road sample spacing

# --- 3. ventilation amplifier from the v1 stagnation score S (0..100) ----------------------------------
VENT_GAIN = 1.0  # A(t) = 1 + VENT_GAIN * S / 100

# --- 4. greenery shield (capped: evidence on vegetation and street-level PM is mixed) -------------------
GREEN_CAP = 0.30
GREEN_W_CANOPY, GREEN_W_NDVI = 0.5, 0.5
GREEN_BUFFER_M = 50.0
NDVI_LOW, NDVI_HIGH = 0.2, 0.8  # NDVI mapped to 0..1 between these

# --- 6. heat burden (summer only) ---------------------------------------------------------------------
HEAT_RANGE_K = 8.0
HEAT_GATE_FROM_C, HEAT_GATE_RANGE_C = 26.0, 8.0  # apparent temperature
HEAT_MAX_SHARE = 0.5  # at most half the cost can come from heat

# --- alert thresholds on the city-level PM2.5 (what the banner, chart and advice use) --------------------------------
# 15 = WHO 24 h guideline, 25 = EU 24 h limit (the same anchors as AIR_PM below), 50 = level at which even healthy people should cut
# exertion (EAQI "poor" starts at 50 for hourly PM2.5). Applied to hourly values, so they are a screening cut, not a compliance test.
PM_WATCH, PM_WARNING, PM_EVERYONE = 15.0, 25.0, 50.0
ALERT_MIN_HOURS = 3  # the banner turns red only when the level stays at or above PM_WARNING this many hours in a row
# Probabilistic alert (backend/ml/backtest.py writes the fitted values into the model file; these are the fallbacks):
ALERT_P = 0.7  # red when P(>= PM_WARNING for ALERT_MIN_HOURS in a row, next 24 h) reaches this: maximises Peirce skill on heating season 2024/25
ALERT_P_WATCH = 0.2  # amber ("episode possible") from here. A policy choice (a 1-in-5 chance is worth a heads-up), not fitted
ALERT_PHI = 0.88  # lag-1 correlation of the level error between consecutive hours (back-test)
# Maps PM2.5 onto the 0-100 stagnation-score scale for code that still mixes the two (the exercise-slot picker): PM_WARNING == 60.
PM_TO_SCORE = 60.0 / PM_WARNING

# --- 7. outputs ------------------------------------------------------------------------------------------
# PM2.5 (ug/m3) -> air score (100 clean .. 0 worst), piecewise linear through health anchors rather than one straight line to 60:
# 5 = WHO annual guideline, 15 = WHO 24 h guideline, 25 = EU 24 h limit. A calm autumn day (3 ug/m3) is therefore ~88, not ~95,
# so streets stay distinguishable instead of the whole city being one shade of green.
AIR_PM = (0.0, 5.0, 15.0, 25.0, 50.0, 100.0)
AIR_SCORE = (100.0, 80.0, 60.0, 40.0, 15.0, 0.0)
PM_NORM = AIR_PM[-1]  # ug/m3 at which the displayed index saturates

# --- routing ---------------------------------------------------------------------------------------------
WALK_KMH = 4.8
STEPS_FACTOR = 1.5
BIKE_KMH = 15.0
# Relative ventilation rate used for dose: an ASSUMPTION (cycling is commonly cited at 2-3x walking). Scales dose, not route choice.
BREATHING = {"walk": 1.0, "bike": 2.0}
# Routing weighs PM2.5 against the city's own spread (p5..p95 over all streets), not the absolute scale: on a clean day the
# absolute index is almost flat (all streets 90-96), which made every route identical. The floor stops a perfectly flat field
# from turning measurement noise into detours.
ROUTE_MIN_SPREAD = 3.0  # ug/m3
ROUTE_REL_MAX = 2.0  # cap on the relative burden so a single hot spot cannot dominate the cost
ROUTE_K = 2.0  # cost = time * (1 + lambda * cost_burden * ROUTE_K)
ROUTE_MAX_EXTRA = 0.30  # cleanest/balanced routes are capped at +30 % time over fastest
LAMBDAS = {"fastest": 0.0, "balanced": 1.0, "cleanest": 3.0}
# Pollen-aware routing (EXPERIMENTAL, an ASSUMPTION): for an allergic user the street's burden grows by POLLEN_GREEN_WEIGHT * level * G, where
# level is the user's pollen level that hour (0 none .. 3 high) and G the greenery 0..1 (tree canopy + NDVI). Greenery is only a proxy for
# the plants that shed pollen; nothing here is validated against exposure or symptoms.
POLLEN_GREEN_WEIGHT = 0.3
SNAP_MAX_M = 500.0  # origin/destination must be within this of the walking network
GOOGLE_SNAP_M = 25.0

# --- network visualisation --------------------------------------------------------------------------------
NETWORK_N_OD = 400
NETWORK_ORIGINS = 40  # 40 origins x 10 destinations = 400 OD pairs, one Dijkstra tree per origin
NETWORK_OD_MIN_M, NETWORK_OD_MAX_M = 1000.0, 4500.0
NETWORK_OD_BIKE_M = (2500.0, 9000.0)  # cyclists travel further
AREA_CELL_M = 250.0
AREA_MIN_STREET_M = 150.0  # cells with less street length than this (fields, forest) are not drawn
CHANNEL_MIN_USE = 3  # a clean channel is used by at least this many of the sample cleanest routes ...
CHANNEL_CAI_PCTL = 60  # ... and sits above this percentile of the city's CAI
NETWORK_SEED = 20261017
CHOKEPOINT_TOP = 15  # shown
CHOKEPOINT_POOL = 40  # ranked internally; the API keeps the top ones inside Sofia's 24 districts
