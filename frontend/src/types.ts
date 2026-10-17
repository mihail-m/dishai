export type Level = "good" | "watch" | "warning";
export type Scenario = "live" | "winter_inversion" | "summer_heatwave" | "birch_season";
export type Profile = "driver" | "wood_heater" | "vulnerable";
export type Toggle = "pollen_allergy" | "asthma" | "heat_sensitive" | "outdoor_exercise" | "kids_or_elderly";
export type Mode = "walk" | "bike";

export interface Hour {
  time: string; score: number; label: Level; blh: number; wind10m: number; t2m: number; t925: number;
  inversion_delta: number; wind925: number; cloud: number; apparent?: number; radiation?: number | null; rh?: number | null;
  pm25: number | null; pm10: number | null; aqi: number | null; is_night: boolean;
  /** learned city-level PM2.5 (the number every screen uses); `pm25` above is the raw CAMS regional forecast */
  pm25_model: number | null; level: Level;
  /** 10th and 90th percentile of the learned city-level estimate (null when the model has no interval) */
  pm25_p10?: number | null; pm25_p90?: number | null;
  pollen?: Record<string, number | null> | null;
}
export interface Alert {
  active: boolean; in_progress?: boolean; start?: string; end?: string; start_hhmm?: string; end_hhmm?: string;
  hours?: number; peak_score: number; peak_score_time: string | null; peak_time: string | null; message: string;
  level: Level; label: string; audience: "none" | "sensitive" | "everyone";
  mean_pm25?: number; peak_pm25: number | null; trapped: boolean;
  basis: "model" | "cams_bias" | "stagnation"; scale: "sensor" | "reference";
  /** chance (0-1) that the city level stays at or above the EU limit for 3 h in a row within 24 h; null without an interval model */
  probability: number | null; p_red?: number; p_watch?: number; peak_p90?: number | null;
}
export interface Window { start: string; end: string; start_hhmm: string; end_hhmm: string }
export interface Forecast {
  scenario: string | null; source: string; sources: Record<string, string>; generated_at: string;
  now_index: number; alert: Alert; best_window: Window | null; hours: Hour[]; note: string;
}
export interface ForecastWindow {
  id: string; forecast: Forecast; hours: number[]; values: CaiValues[]; sensors: SensorsResp;
}
export interface Sensor {
  id: number | string; lat: number; lon: number; pm25: number; pm10: number | null; district: string | null;
  source?: "sensor_community" | "airthings"; name?: string; timestamp?: string;
}
export interface SensorsResp {
  scenario: string | null; simulated: boolean; source: string; count: number; median_pm25: number | null;
  label: string; sensors: Sensor[];
  airthings?: { enabled: boolean; count: number; source: string | null; attribution: string };
}
export interface DistrictProps {
  name: string; elevation_factor: number; approx: boolean; score: number; label: Level;
  peak_24h: number; peak_24h_label: Level; sensor_count: number; median_pm25: number | null; centroid: [number, number];
  /** PM2.5 alert for the district (city forecast moved by its sensors) */
  level_pm?: Level; p_episode?: number | null; peak_pm25?: number | null;
}
export type DistrictsFC = GeoJSON.FeatureCollection<GeoJSON.Geometry, DistrictProps> & {
  meta: { time: string; hour_index: number; scenario: string | null; source: string; note: string };
};
export interface Card {
  id: string; title: string; body: string; severity: "info" | "action"; cta?: string;
  measure: { id: string; label: string } | null;  // null: advice with no Sofia climate-plan measure behind it
}
export interface HeatSummary {
  level: "none" | "caution" | "high" | "extreme"; peak_c: number | null; peak_hhmm: string | null;
  start_hhmm: string | null; end_hhmm: string | null; warm_night: boolean;
}
export interface PollenSummary {
  available: boolean; level: "none" | "low" | "moderate" | "high"; peak_hhmm: string | null;
  types: { type: string; label: string; grains: number; level: string; peak_hhmm: string }[];
}
export interface Conditions { heat: HeatSummary; pollen: PollenSummary; activity: { start_hhmm: string; end_hhmm: string } | null }
export interface Advice {
  profile: Profile; toggles: Toggle[]; district: string; conditions: Conditions;
  status: { level: Level; current_score: number; peak_score: number; peak_pm25: number | null; alert: Alert; best_window: Window | null };
  zone: { zone: "inner" | "outer" | null; illustrative: boolean; label: string; basis: string };
  cards: Card[]; grant_note: string;
}
export interface DistrictRow {
  district: string; peak_24h: number; level: Level; p_episode?: number | null; leads: number; sample_leads: number; submitted_leads: number;
  eligible_submitted: number; hotspot_index: number; sensors: number;
}
export interface Dashboard {
  scenario: string | null; source: string; alert: Alert; hotspots: DistrictRow[]; leads_by_district: DistrictRow[];
  leads_total: number; leads_submitted: number;
  street_washing: { rank: number; district: string; street: string; district_rank: number; peak_24h: number; sample: boolean }[];
  street_washing_note: string; sensors_active: number; sensors_simulated: boolean;
}

// ---- PLAN2: Clean Air Index, routes, network ------------------------------------------------------
export interface CaiValues {
  hour: number; time: string; scenario: string | null; cai: ArrayLike<number>; pm25?: number[]; background: number; bias: number;
  heat_weight: number; stagnation: number;
}
export interface EdgeExplain {
  id: number; edge: number; time: string; pm25: number; cai: number; street: string | null; street_bg: string | null; near_street: boolean;
  district: string | null; geometry: [number, number][]; road_class: string; length_m: number;
  components_ugm3: { background: number; local_correction: number; ventilation: number; greenery: number };
  drivers: { greenery: number; heat_anomaly: number; heat_weight: number; stagnation_score: number; construction_share?: number };
  /** measured on the arterial corridor that runs along this street (Google + TomTom); null where nothing is measured. Not part of the PM2.5 estimate. */
  observed_traffic?: ObservedTraffic | null;
  heat_points: number; cai_air_only: number; note: string;
}
export interface RouteStats {
  time_min: number; distance_m: number; mean_pm25: number; dose: number; mean_cai: number | null; lambda_used?: number;
  dose_change_pct: number; time_change_pct?: number; polyline: [number, number][]; steps_m?: number; eta_min?: number; snapped_share?: number;
}
export interface RouteResp {
  google_available?: boolean;
  from: [number, number]; to: [number, number]; from_label: string; to_label: string; hour: number; time: string; scenario: string | null;
  routes: { fastest: RouteStats; balanced: RouteStats; cleanest: RouteStats; chosen?: RouteStats };
  google_status: "ok" | "no_key" | "error" | "n/a"; mode?: Mode; google_baseline: RouteStats | null;
  /** is any alternative distinguishable from the fastest route, given the model's street-level uncertainty? null without a spatial fit */
  confidence?: { cleaner_exists: boolean; better: string[]; message: string | null;
    routes: Record<string, { diff_pct: number; sd_pct: number; distinguishable: boolean; unshared_m: [number, number] }> } | null;
  snapped: { used: boolean; polyline: [number, number][]; eta_min: number | null; waypoints: [number, number][] };
  open_in_google_maps: string; open_in_google_maps_clean: string; note: string;
}
export interface Chokepoint {
  rank: number; street: string; street_bg: string; district: string; road_class: string; score: number; max_fast_usage: number; mean_cai: number; length_m: number;
  lon: number; lat: number; segments: [number, number][][]; measures: { id: string; label: string; why: string }[];
}
export interface NetworkResp {
  n_od: number; hour: number; time: string; scenario: string | null; median_dose_change_pct: number; median_time_change_pct: number;
  share_cleaner: number; when_cleaner: { n: number; median_dose_change_pct: number; median_time_change_pct: number }; note: string; chokepoints: Chokepoint[]; trips: [number, number][][];
  segments: { id: number; path: [number, number][]; usage_clean: number; usage_fast: number; cai: number; channel: boolean }[];
  mode: Mode; channel_cai_cut: number; n_channel_segments: number;
}
export interface ValidationBlock {
  n: number; spearman: number | null; median_abs_error: number; verdict: "weak" | "moderate" | "reasonable"; hour: string;
  baselines: { no_sensor_residual: { spearman: number | null; median_abs_error: number }; constant_median: { median_abs_error: number }; background_only: { median_abs_error: number } };
  observed: { median: number; p10: number; p90: number }; caveat: string;
}
export interface MlMetrics { mae: number; median_abs_error: number; spearman_within_hour: number | null }
export interface MlEpisode { mae: number; bias: number }
export interface MlValidation {
  trained: string | null; span: [string, string] | null; sensors: number | null;
  n?: number; spearman?: number | null; median_abs_error?: number; error?: string;
  ml_only?: { spearman: number | null; median_abs_error: number };
  // hidden sensors AND future days (never seen in training)
  offline_eval: { ml: MlMetrics; cams_x_bias: MlMetrics; persistence_city_median: MlMetrics } | null;
  // hours where CAMS forecasts >= 50 ug/m3
  episodes: { rows: number; ml: MlEpisode; cams_x_bias: MlEpisode; persistence_city_median: MlEpisode } | null;
}
export interface SpatialCut { hours: number; coverage80: number; level_trend: { median_abs_error: number; within_hour_spearman: number | null };
  idw_current: { median_abs_error: number; within_hour_spearman: number | null }; krige_offsets_removed: { median_abs_error: number; within_hour_spearman: number | null } }
export interface Trust {
  calibration?: { reference: string; rows: number; sensors: number; raw_mae: number; calibrated_mae: number | null; mae_reduction_pct: number | null;
    ratio_by_rh: Record<string, number>; typical_error_pct: number; one_reference_station: boolean };
  spatial?: { offset_sd_pct: number; offset_stability_r: number; range_km: number | null; nugget_share_pct: number | null;
    polluted_heating: SpatialCut | null; heating: SpatialCut | null; summer: SpatialCut | null; all: SpatialCut | null };
  street_vs_area?: Record<string, unknown>;
  alert?: { p_red: number; p_watch: number; selected_on: string; test_period: Record<string, { alerts: number; pod: number; far: number; peirce: number; missed_episodes: number }> };
}
export interface Validation extends ValidationBlock { daily_mean: ValidationBlock | null; ml?: MlValidation | null; trust?: Trust; warning?: string; scenario: string | null }

export interface RefStation {
  uid: number; name: string; name_bg: string | null; lat: number; lon: number; time: string | null;
  pm10: number | null; pm10_aqi: number | null; no2_aqi: number | null; o3_aqi: number | null; co_aqi: number | null;
}
export interface ReferenceResp {
  enabled: boolean; reason?: string; attribution: string; pm25_available: boolean; note: string; stations: RefStation[];
  sensor_check?: { radius_m: number; median_ratio: number | null; verdict?: string; rows: { station: string; n_sensors: number; citizen_pm10: number; reference_pm10: number; ratio: number | null }[] };
}

export interface AreaCells { lon: number[]; lat: number[]; mean: number[]; max: number[]; min: number[]; street_m: number[]; n: number[]; pm25?: number[]; anomaly?: number[] }
export interface AreaPlace { lon: number; lat: number; value: number; street_m: number; place: string }
export interface AreasResp {
  metric: "air" | "heat"; mode: Mode; cell_m: number; unit: string; hour?: number; time?: string; scenario?: string | null; reference?: number;
  thresholds: { clean?: number; dirty?: number; hot?: number; cool?: number }; stats: { median: number; p10: number; p90: number; min: number; max: number };
  cells: AreaCells; extremes: { best: AreaPlace[]; worst: AreaPlace[] }; note: string;
}

// ---- real traffic (Google Routes + TomTom) ----------------------------------------------------------------
export interface TrafficCorridor {
  id: string; name: string; polyline: [number, number][]; length_km: number;
  live_index: number | null; typical_index: number | null; vs_typical: number | null;
  google: { duration_s?: number; slow_share?: number; jam_share?: number; tti?: number };
  tomtom: { tti?: number; points?: number };
}
export interface TrafficResp {
  enabled: boolean; google: boolean; tomtom: boolean; ready: boolean; measured_profile: boolean; updated: string | null;
  corridors: TrafficCorridor[]; profile: { wk: number[]; we: number[] } | null; volume_profile?: VolumeProfile | null;
  usage: { day: string; google_requests: number; tomtom_requests: number }; note: string; sources: string[];
}
export interface Incident {
  type: "Feature"; geometry: { type: "Point"; coordinates: [number, number] } | { type: "LineString"; coordinates: [number, number][] };
  properties: { id: string; category: string; category_code: number; magnitude: string; magnitude_code: number; description: string;
    from: string | null; to: string | null; length_m: number; delay_s: number | null; road: string; start: string | null; end: string | null; reports: number | null };
}
export interface IncidentsResp { type: "FeatureCollection"; features: Incident[]; enabled: boolean; count?: number; source?: string; updated?: string; error?: boolean }

// ---- Google Air Quality + Pollen -----------------------------------------------------------------------------
export interface GoogleAir {
  enabled: boolean; updated?: string; note?: string; source?: string;
  current?: { time: string; indexes: { code: string; name: string; aqi: number; category: string; dominant: string }[];
    pollutants: Record<string, { name: string; value: number | null; unit: string }>; health: Record<string, string> } | null;
  forecast?: { time: string; pm25: number | null; pm10: number | null; no2: number | null; uaqi: number | null }[] | null;
  pollen?: { date: string; types: Record<string, { name: string; in_season: boolean; value: number | null; category: string | null; advice: string | null }>;
    plants: { name: string; type: string; value: number; category: string }[] }[] | null;
}

export interface ObservedTraffic {
  corridor: string; live_index: number; typical_index: number | null; vs_typical: number | null;
  google_min: number | null; tomtom_index: number | null; jam_share: number | null;
}

/** Measured typical vehicle volume by local hour, as a share of the weekday peak (BGTOLL open data, CC BY 4.0). */
export interface VolumeProfile {
  source: string; points: { scp: number; name: string }[]; from: string; to: string; hours_used: number;
  weekday: number[]; weekend: number[]; heavy_share: { weekday: number; weekend: number }; note: string;
}

// ---- NO2 with measured traffic (backend/no2_model.py) -----------------------------------------------------------
export interface No2Station { station: string; type: string; elasticity: number; lo: number; hi: number; cv_gain_pct_no_hour_fe: number; cv_gain_pct_with_hour_fe: number; hours: number }
export interface No2Resp {
  enabled: boolean;
  forecast?: { time: string; no2: number; p10: number; p90: number; with_cams: boolean }[]; mean_24h?: number | null; who_24h?: number;
  scenarios?: { traffic_cut_pct: number; no2_change_pct: number; range_pct: [number, number] }[];
  traffic_profile?: { weekday: number[]; weekend: number[] };
  study?: {
    weekday_weekend_ratio: { no2: number; traffic: number }; diurnal_no2: { weekday: number[]; weekend: number[] };
    stations: No2Station[]; pm: { pm25: No2Station; pm10: No2Station }; reading: string;
  };
  eval?: Record<string, { rmse_log: number; skill_pct: number } | number>;
  elasticity?: { mean: number; lo: number; hi: number };
  trained?: string; range?: [string, string]; n_hours?: number; stations?: string[]; caveats?: string[];
}
