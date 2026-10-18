export type DataMode = 'demo' | 'live';
export type RouteId = 'fast' | 'clean';
/** All map coordinates use GeoJSON order: longitude, latitude. */
export type Coordinate = [number, number];

export interface ForecastHour {
  index: number;
  backendHour: number;
  time: string;
  label: string;
  pm25: number | null;
  pm25Source: 'demo' | 'model' | 'regional' | 'unavailable';
  temperature: number | null;
  wind: number | null;
  condition: 'clear' | 'partly-cloudy' | 'cloudy' | 'night';
  /** Relative position within this forecast, not an AQI or a health threshold. */
  quality: 'lower' | 'moderate' | 'elevated' | 'unavailable';
}

export interface BestWindow {
  startIndex: number;
  endIndex: number;
  meanPm25: number;
}

export interface Sensor {
  id: string;
  coordinates: Coordinate;
  pm25: number;
  name: string;
  timestamp?: string;
}

export interface ForecastData {
  id: string;
  mode: DataMode;
  generatedAt: string;
  source: string;
  note: string;
  hours: ForecastHour[];
  bestWindow: BestWindow | null;
  sensors: Sensor[];
}

export interface Place {
  label: string;
  coordinates: Coordinate;
}

export interface RouteOption {
  id: RouteId;
  title: string;
  minutes: number;
  distanceKm: number;
  meanPm25: number;
  /** Negative means less estimated exposure than the fastest route. */
  doseChangePct: number;
  polyline: Coordinate[];
}

export interface RouteData {
  mode: DataMode;
  hour: number;
  time: string;
  from: Place;
  to: Place;
  routes: { fast: RouteOption; clean: RouteOption };
  confidence: { cleanerExists: boolean | null; message: string | null };
  note: string;
}
