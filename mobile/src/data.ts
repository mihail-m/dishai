import { DEMO_TRIP, getDemoRouteOptions, resolveDemoPlace } from './route-model';
import { parseStreetValues } from './map-air';
import type { BestWindow, Coordinate, DataMode, ForecastData, ForecastHour, RouteData, RouteId, RouteOption, RoutePlan, Sensor } from './types';

export type { BestWindow, Coordinate, DataMode, ForecastData, ForecastHour, Place, RouteData, RouteId, RouteOption, RoutePlan, Sensor } from './types';
export { DEMO_TRIP, DEMO_MAP_BOUNDS, ROUTE_PLACES, searchRoutePlaces, resolveDemoPlace } from './route-model';

const forecastCache = new Map<DataMode, ForecastData>();
const forecastCachedAt = new Map<DataMode, number>();
const FORECAST_CACHE_MS = 5 * 60 * 1000;
const routeCache = new Map<string, RouteData>();
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');
const round = (value: number, places = 1) => Math.round(value * 10 ** places) / 10 ** places;
const numberOrNull = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const nonnegativeOrNull = (value: unknown): number | null => { const n = numberOrNull(value); return n !== null && n >= 0 ? n : null; };
const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const invalidForecast = () => new Error('The service did not return a complete 24-hour forecast. Refresh to try again.');
const abortIfNeeded = (signal?: AbortSignal) => signal?.throwIfAborted();

/** Backend timestamps are already local to Sofia. Do not shift them through the laptop timezone. */
export function formatTime(time: string): string { return time.slice(11, 16); }

/** End of an hourly bucket, preserving the service's Sofia wall-time representation and offset. */
export function windowEndTime(time: string): string {
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(:\d{2}(?:\.\d+)?)?([Zz]|[+-]\d{2}:\d{2})?$/.exec(time);
  if (!parts) throw new Error('The forecast contains an invalid time.');
  const [, year, month, day, hour, minute, seconds = '', suffix = ''] = parts;
  const next = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour) + 1, Number(minute)));
  return `${next.toISOString().slice(0, 16)}${seconds}${suffix}`;
}

/** Lowest complete consecutive window; never treats a missing concentration as clean air. */
export function chooseBestWindow(hours: readonly Pick<ForecastHour, 'pm25'>[], duration = 2): BestWindow | null {
  if (!Number.isInteger(duration) || duration < 1 || duration > hours.length) return null;
  let best: BestWindow | null = null;
  for (let start = 0; start <= hours.length - duration; start++) {
    const values = hours.slice(start, start + duration).map((hour) => hour.pm25);
    if (values.some((value) => value === null || !Number.isFinite(value) || value < 0)) continue;
    const mean = (values as number[]).reduce((sum, value) => sum + value, 0) / duration;
    if (best === null || mean < best.meanPm25) best = { startIndex: start, endIndex: start + duration - 1, meanPm25: mean };
  }
  return best ? { ...best, meanPm25: round(best.meanPm25) } : null;
}

function assignRelativeQuality(hours: ForecastHour[]): ForecastHour[] {
  const sorted = hours.map((hour) => hour.pm25).filter((value): value is number => value !== null).sort((a, b) => a - b);
  const low = sorted[Math.floor((sorted.length - 1) / 3)];
  const high = sorted[Math.ceil((sorted.length - 1) * 2 / 3)];
  return hours.map((hour) => ({ ...hour, quality: hour.pm25 === null ? 'unavailable'
    : low === high ? 'moderate' : hour.pm25 <= low ? 'lower' : hour.pm25 >= high ? 'elevated' : 'moderate' }));
}

function makeDemoForecast(): ForecastData {
  // Deliberately fixed: recording the same scenario is repeatable, including without a network.
  const concentrations = [14.2, 12.8, 11.1, 9.4, 7.8, 6.6, 6.2, 7.1, 8.8, 12.4, 17.3, 19.1, 16.5, 14.2, 12.3, 10.4, 9.8, 9.2, 9.5, 10.8, 13.6, 18.4, 21.2, 17.1];
  const temperatures = [18, 19, 20, 21, 22, 23, 23, 22, 21, 20, 18, 17, 16, 15, 14, 13, 12, 12, 12, 13, 14, 15, 16, 17];
  const hours = assignRelativeQuality(concentrations.map((pm25, index) => {
    const stamp = new Date(Date.UTC(2026, 9, 18, 9 + index));
    const hour = stamp.getUTCHours();
    const time = `${stamp.toISOString().slice(0, 16)}:00+03:00`;
    return { index, backendHour: index, time, label: formatTime(time), pm25, pm25Source: 'demo' as const,
      temperature: temperatures[index], wind: round(4.2 + Math.sin(index / 4) * 1.7),
      condition: (hour >= 19 || hour < 7 ? 'night' : index < 4 ? 'partly-cloudy' : 'clear') as ForecastHour['condition'], quality: 'moderate' as const };
  }));
  return { id: 'mobile-demo-sofia-2026-10-18', mode: 'demo', generatedAt: '2026-10-18T09:00:00+03:00',
    source: 'Illustrative demo scenario',
    note: 'Sample forecast for a repeatable demo. Lower and elevated compare hours within this forecast; they are not health ratings or an official air-quality index.',
    hours, bestWindow: chooseBestWindow(hours), sensors: [] };
}

/** Validate the wire contract and preserve backend hour IDs for snapshot-bound routing. */
export function parseForecastWindow(payload: unknown): ForecastData {
  if (!isRecord(payload) || typeof payload.id !== 'string' || !payload.id || !isRecord(payload.forecast)
    || !Array.isArray(payload.hours) || !Array.isArray(payload.values)) throw invalidForecast();
  const forecast = payload.forecast;
  if (!Array.isArray(forecast.hours) || forecast.hours.length < 24 || payload.hours.length < 24 || payload.values.length < 24
    || typeof forecast.generated_at !== 'string' || typeof forecast.source !== 'string') throw invalidForecast();
  const indices = payload.hours;
  const values = payload.values;
  const rows = forecast.hours.slice(0, 24);
  let previousTime: number | null = null;
  const hours = assignRelativeQuality(rows.map((row: unknown, index): ForecastHour => {
    const backendHour = indices[index];
    const value = values[index];
    if (!isRecord(row) || typeof row.time !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(row.time)
      || !Number.isInteger(backendHour) || (backendHour as number) < 0 || !isRecord(value) || value.hour !== backendHour || value.time !== row.time
      || (index > 0 && backendHour !== (indices[index - 1] as number) + 1)) throw invalidForecast();
    // Naive service timestamps use Sofia wall time. Use a neutral timezone only to check hourly spacing.
    const timestamp = Date.parse(/[Zz]$|[+-]\d{2}:\d{2}$/.test(row.time) ? row.time : `${row.time}Z`);
    if (!Number.isFinite(timestamp) || (previousTime !== null && timestamp - previousTime !== 3_600_000)) throw invalidForecast();
    previousTime = timestamp;
    const model = nonnegativeOrNull(row.pm25_model);
    const regional = nonnegativeOrNull(row.pm25);
    const cloud = numberOrNull(row.cloud);
    return { index, backendHour: backendHour as number, time: row.time, label: formatTime(row.time),
      pm25: model ?? regional, pm25Source: model !== null ? 'model' : regional !== null ? 'regional' : 'unavailable',
      temperature: numberOrNull(row.t2m), wind: nonnegativeOrNull(row.wind10m),
      condition: row.is_night === true ? 'night' : cloud === null || cloud < 25 ? 'clear' : cloud < 70 ? 'partly-cloudy' : 'cloudy',
      quality: 'moderate' };
  }));
  const sensors: Sensor[] = [];
  if (isRecord(payload.sensors) && Array.isArray(payload.sensors.sensors)) {
    for (const item of payload.sensors.sensors) {
      if (!isRecord(item)) continue;
      const lat = numberOrNull(item.lat), lon = numberOrNull(item.lon), pm25 = nonnegativeOrNull(item.pm25);
      if (lat === null || lon === null || pm25 === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) continue;
      sensors.push({ id: String(item.id ?? sensors.length), coordinates: [lon, lat], pm25,
        name: typeof item.name === 'string' ? item.name : typeof item.district === 'string' ? item.district : 'Community sensor',
        ...(typeof item.timestamp === 'string' ? { timestamp: item.timestamp } : {}) });
    }
  }
  return { id: payload.id, mode: 'live', generatedAt: forecast.generated_at, source: forecast.source,
    note: [forecast.source === 'fixture' ? 'The backend is using a saved offline fixture; this is not a current live forecast.' : '',
      typeof forecast.note === 'string' ? forecast.note : '', 'Lower and elevated compare hours within this forecast; they are not health ratings or an official air-quality index.'].filter(Boolean).join(' '),
    hours, bestWindow: chooseBestWindow(hours), sensors, streetValues: parseStreetValues(values) };
}

async function request(path: string, signal?: AbortSignal, refresh = false): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { signal, method: refresh ? 'POST' : 'GET', cache: 'no-store' });
  } catch (error) {
    abortIfNeeded(signal);
    throw new Error('Cannot reach the DishAI service. Start the backend and check the API address, or choose Demo mode.', { cause: error });
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = isRecord(payload) && typeof payload.detail === 'string' ? payload.detail : `The service returned HTTP ${response.status}.`;
    throw new Error(detail);
  }
  if (payload === null) throw new Error('The service returned an unreadable response. Check the API address and refresh.');
  return payload;
}

export async function loadForecast({ mode, force = false, signal }: { mode: DataMode; force?: boolean; signal?: AbortSignal }): Promise<ForecastData> {
  abortIfNeeded(signal);
  const cached = forecastCache.get(mode);
  if (!force && cached && (mode === 'demo' || Date.now() - (forecastCachedAt.get(mode) ?? 0) < FORECAST_CACHE_MS)) return cached;
  const forecast = mode === 'demo' ? makeDemoForecast()
    : parseForecastWindow(await request(`/api/forecast/${force ? 'refresh' : 'window'}`, signal, force));
  abortIfNeeded(signal);
  forecastCache.set(mode, forecast);
  forecastCachedAt.set(mode, Date.now());
  // Keep successful old forecast data if a refresh fails, but discard obsolete route snapshots after success.
  for (const key of routeCache.keys()) if (key.startsWith(`${mode}:`)) routeCache.delete(key);
  return forecast;
}

function parseRouteOption(value: unknown, id: RouteId): RouteOption {
  if (!isRecord(value) || !Array.isArray(value.polyline) || value.polyline.length < 2
    || nonnegativeOrNull(value.time_min) === null || nonnegativeOrNull(value.distance_m) === null
    || nonnegativeOrNull(value.mean_pm25) === null || numberOrNull(value.dose_change_pct) === null) throw new Error('The service returned an incomplete route. Try again.');
  const polyline: Coordinate[] = value.polyline.map((point: unknown) => {
    if (!Array.isArray(point) || point.length < 2 || numberOrNull(point[0]) === null || numberOrNull(point[1]) === null
      || Math.abs(point[0] as number) > 180 || Math.abs(point[1] as number) > 90) throw new Error('The service returned invalid route geometry.');
    return [point[0] as number, point[1] as number];
  });
  return { id, title: id === 'fast' ? 'Fastest' : id === 'balanced' ? 'Balanced' : 'Cleanest', minutes: value.time_min as number,
    distanceKm: (value.distance_m as number) / 1000, meanPm25: value.mean_pm25 as number,
    doseChangePct: value.dose_change_pct as number, polyline };
}

export interface RouteRequest { mode: DataMode; hour: number; forecastId?: string; signal?: AbortSignal; reversed?: boolean; plan?: RoutePlan }

export async function loadRoutes({ mode, hour, forecastId, signal, reversed = false, plan = DEMO_TRIP }: RouteRequest): Promise<RouteData> {
  abortIfNeeded(signal);
  if (!Number.isInteger(hour) || hour < 0 || hour > 23) throw new Error('Select an hour within the 24-hour forecast.');
  const forecast = forecastCache.get(mode) ?? await loadForecast({ mode, signal });
  if (forecastId && forecastId !== forecast.id) throw new Error('The forecast has changed. Update the routes using the latest forecast.');
  const requestedFrom = reversed ? plan.to : plan.from;
  const requestedTo = reversed ? plan.from : plan.to;
  const validatePlace = (place: RoutePlan['from']) => {
    if (!place || !Array.isArray(place.coordinates) || place.coordinates.length !== 2 || !place.coordinates.every(Number.isFinite)
      || Math.abs(place.coordinates[0]) > 180 || Math.abs(place.coordinates[1]) > 90) throw new Error('Choose valid starting and destination locations.');
    return mode === 'demo' ? resolveDemoPlace(place.coordinates, place.label) : { ...place, coordinates: [...place.coordinates] as Coordinate };
  };
  const from = validatePlace(requestedFrom), to = validatePlace(requestedTo);
  const key = `${mode}:${forecast.id}:${hour}:${JSON.stringify([from, to])}`;
  const cached = routeCache.get(key);
  if (cached) return cached;
  const selectedHour = forecast.hours[hour];
  let result: RouteData;
  if (mode === 'demo') {
    result = { mode, hour, time: selectedHour.time, from, to,
      routes: getDemoRouteOptions({ from, to }, selectedHour.pm25 ?? 14.2),
      confidence: { cleanerExists: null, message: 'Illustrative exposure comparison.' },
      note: 'Demo routes follow the local OpenStreetMap street network; selected points snap to nearby connected paths. Exposure values use illustrative street-class factors, not measured or model-validated pollution. Alternatives can overlap where the network has limited choices.' };
  } else {
    // Existing backend accepts latitude,longitude; map data and public mobile types use longitude,latitude.
    const query = new URLSearchParams({ from: `${from.coordinates[1]},${from.coordinates[0]}`, to: `${to.coordinates[1]},${to.coordinates[0]}`,
      hour: String(selectedHour.backendHour), mode: 'walk', forecast_id: forecast.id, include_google: 'false' });
    const payload = await request(`/api/route?${query}`, signal);
    if (!isRecord(payload) || !isRecord(payload.routes) || payload.hour !== selectedHour.backendHour || payload.time !== selectedHour.time) throw new Error('The route response does not match the selected forecast hour. Refresh and try again.');
    const confidence = isRecord(payload.confidence) ? payload.confidence : null;
    const confidenceRoutes = confidence && isRecord(confidence.routes) ? confidence.routes : null;
    const routeImprovement = (name: 'balanced' | 'cleanest') => {
      const routeConfidence = confidenceRoutes && isRecord(confidenceRoutes[name]) ? confidenceRoutes[name] : null;
      return typeof routeConfidence?.distinguishable === 'boolean'
        ? routeConfidence.distinguishable && typeof routeConfidence.diff_pct === 'number' && routeConfidence.diff_pct < 0
        : name === 'cleanest' && typeof confidence?.cleaner_exists === 'boolean' ? confidence.cleaner_exists : null;
    };
    const cleanerExists = routeImprovement('cleanest');
    const clean = parseRouteOption(payload.routes.cleanest, 'clean');
    const balanced = parseRouteOption(payload.routes.balanced, 'balanced');
    clean.cleanerExists = cleanerExists;
    balanced.cleanerExists = routeImprovement('balanced');
    // A lower model estimate alone is not evidence of a distinguishable improvement.
    if (cleanerExists === false || clean.doseChangePct >= 0) clean.title = 'Alternative route';
    if (balanced.cleanerExists === false || balanced.doseChangePct >= 0) balanced.title = 'Alternative route';
    result = { mode, hour, time: selectedHour.time, from, to, routes: { fast: parseRouteOption(payload.routes.fastest, 'fast'), balanced, clean },
      confidence: { cleanerExists, message: typeof confidence?.message === 'string' ? confidence.message : null },
      note: typeof payload.note === 'string' ? payload.note : 'Indicative route comparison using modelled PM2.5.' };
  }
  abortIfNeeded(signal);
  routeCache.set(key, result);
  while (routeCache.size > 96) routeCache.delete(routeCache.keys().next().value!);
  return result;
}

/** Optional route preloading; all forecast values are already local before this starts. */
export async function preloadRoutes({ forecast, signal, reversed = false, plan = DEMO_TRIP, onProgress }: {
  forecast: ForecastData; signal?: AbortSignal; reversed?: boolean; plan?: RoutePlan; onProgress?: (loaded: number, total: number) => void;
}): Promise<RouteData[]> {
  abortIfNeeded(signal);
  const results: RouteData[] = new Array(forecast.hours.length);
  let next = 0;
  let loaded = 0;
  let failure: unknown;
  onProgress?.(0, forecast.hours.length);
  const worker = async () => {
    while (next < forecast.hours.length && failure === undefined) {
      const index = next++;
      try {
        results[index] = await loadRoutes({ mode: forecast.mode, hour: index, forecastId: forecast.id, signal, reversed, plan });
        abortIfNeeded(signal);
        onProgress?.(++loaded, forecast.hours.length);
      }
      catch (error) { failure = error; }
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  if (failure !== undefined) throw failure;
  return results;
}
