"""Clean Air Index tunables. Everything here is an ASSUMPTION or a heuristic, not a measurement.

Formula: see PLAN2.md section 3.
    PM(e,t)  = B(t) + R(e,t) + T(e) * rush(t) * A(t) * shield(e)
    cost_burden = (1 - g) * clamp(PM/PM_NORM) + g * H(e)        CAI = round(100 * (1 - cost_burden))
"""

# --- bounding box (west, south, east, north) -----------------------------------------------------
BBOX = (23.15, 42.60, 23.55, 42.80)

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

# --- 2. traffic: OSM class weights (relative, tunable) with exp(-d/40 m) decay out to 150 m ------------
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
# Global scale on the traffic increment. PLAN2 weights (gain 1.0) overshot absolute PM2.5 on a clean autumn day
# (24 h-mean leave-one-out median error 3.3 vs 1.7 ug/m3 for a constant). Halved; see /api/validation sensitivity.
TRAFFIC_GAIN = 0.5
TRAFFIC_DECAY_M = 40.0
TRAFFIC_RADIUS_M = 150.0
TRAFFIC_SPACING_M = 5.0  # road is sampled every 5 m; each sample carries w_class * spacing / 100 m
TRAFFIC_GRID_M = 25.0

# 24-value hour-of-day curve (index = local hour). Night 0.4, peaks 1.0 at 7-9 and 17-19.
RUSH = [0.4, 0.4, 0.4, 0.4, 0.45, 0.6, 0.8, 1.0, 1.0, 1.0, 0.8, 0.7,
        0.7, 0.7, 0.75, 0.85, 0.95, 1.0, 1.0, 1.0, 0.8, 0.65, 0.5, 0.4]
WEEKEND_FACTOR = 0.75

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

# --- 7. outputs ------------------------------------------------------------------------------------------
PM_NORM = 60.0  # ug/m3 at which AirNorm saturates

# --- routing ---------------------------------------------------------------------------------------------
WALK_KMH = 4.8
STEPS_FACTOR = 1.5
BIKE_KMH = 15.0
# Relative ventilation rate used for dose: an ASSUMPTION (cycling is commonly cited at 2-3x walking). Scales dose, not route choice.
BREATHING = {"walk": 1.0, "bike": 2.0}
ROUTE_K = 2.0  # cost = time * (1 + lambda * cost_burden * ROUTE_K)
ROUTE_MAX_EXTRA = 0.30  # cleanest/balanced routes are capped at +30 % time over fastest
LAMBDAS = {"fastest": 0.0, "balanced": 1.0, "cleanest": 3.0}
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
