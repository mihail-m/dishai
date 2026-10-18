import mapData from './data/sofia-map.json';
import { exposureFactors } from './route-model';
import type { Coordinate, DataMode, ForecastData, Sensor } from './types';

export interface StreetAirSegment {
  id: number;
  coordinates: Coordinate[];
  name?: string;
  roadClass: 'major' | 'collector' | 'local' | 'path';
}

export interface StreetAirData {
  mode: DataMode;
  forecastId: string;
  segments: StreetAirSegment[];
  /** Scores use the same segment order in every hour. 100 is cleaner; 0 is worse. */
  hours: { time: string; backendHour: number; scores: Uint8Array }[];
  note: string;
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');
const MAX_SEGMENTS = 250_000;
const MAX_POINTS = 2_000_000;
const MAX_GEOMETRY_BYTES = 20_000_000;
const overlayCache = new WeakMap<ForecastData, StreetAirData>();
let liveGeometry: StreetAirSegment[] | undefined;

/** Fixed sample readings, deliberately independent from the forecast time wheel. */
export const DEMO_SENSORS: Sensor[] = [
  { id: 'sample-city-garden', name: 'City Garden sample', coordinates: [23.32514, 42.69515], pm25: 10.6 },
  { id: 'sample-serdika', name: 'Serdika sample', coordinates: [23.32182, 42.69792], pm25: 18.4 },
  { id: 'sample-crystal', name: 'Crystal Garden sample', coordinates: [23.32936, 42.69439], pm25: 11.8 },
  { id: 'sample-pirotska', name: 'Pirotska Street sample', coordinates: [23.31616, 42.70020], pm25: 17.3 },
  { id: 'sample-princes-garden', name: 'Prince’s Garden sample', coordinates: [23.33291, 42.69172], pm25: 12.9 },
  { id: 'sample-rakovski', name: 'Rakovski Street sample', coordinates: [23.32635, 42.69210], pm25: 22.1 },
].map((sensor) => ({ ...sensor, coordinates: sensor.coordinates as Coordinate, timestamp: '2026-10-18T09:00:00+03:00' }));

/** Optional layer data must never prevent the rest of a valid forecast from loading. */
export function parseStreetValues(values: unknown[]): Uint8Array[] | undefined {
  if (values.length < 24) return undefined;
  const decoded: Uint8Array[] = [];
  let count: number | undefined;
  for (const value of values.slice(0, 24)) {
    if (value === null || typeof value !== 'object' || !('cai_base64' in value)) return undefined;
    const base64 = value.cai_base64;
    if (typeof base64 !== 'string' || base64.length === 0 || base64.length > Math.ceil(MAX_SEGMENTS / 3) * 4
      || !/^[A-Za-z0-9+/]+={0,2}$/.test(base64) || base64.length % 4 !== 0) return undefined;
    let binary: string;
    try { binary = atob(base64); } catch { return undefined; }
    if (binary.length === 0 || binary.length > MAX_SEGMENTS || (count !== undefined && binary.length !== count)) return undefined;
    const scores = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    if (scores.some((score) => score > 100)) return undefined;
    count = scores.length;
    decoded.push(scores);
  }
  return decoded;
}

const geometryError = () => new Error('The street map data is incomplete or unsupported. Refresh and try again.');

/** Binary geometry contract shared with the web map; one download serves all 24 hours. */
export function parseStreetGeometry(buffer: ArrayBuffer): StreetAirSegment[] {
  if (buffer.byteLength < 8 || buffer.byteLength > MAX_GEOMETRY_BYTES) throw geometryError();
  const view = new DataView(buffer);
  const count = view.getUint32(0, true), points = view.getUint32(4, true);
  const coordinatesStart = 8 + 4 * (count + 1);
  const classesStart = coordinatesStart + 8 * points;
  if (count < 1 || count > MAX_SEGMENTS || points < count * 2 || points > MAX_POINTS
    || classesStart + count !== buffer.byteLength || view.getUint32(8, true) !== 0
    || view.getUint32(8 + count * 4, true) !== points) throw geometryError();
  const roadClasses = ['major', 'collector', 'local', 'path'] as const;
  const result: StreetAirSegment[] = [];
  for (let id = 0; id < count; id++) {
    const start = view.getUint32(8 + id * 4, true), end = view.getUint32(8 + (id + 1) * 4, true);
    const roadClass = roadClasses[view.getUint8(classesStart + id)];
    if (start >= end || end > points || end - start < 2 || !roadClass) throw geometryError();
    const coordinates: Coordinate[] = [];
    for (let index = start; index < end; index++) {
      const lon = view.getFloat32(coordinatesStart + index * 8, true);
      const lat = view.getFloat32(coordinatesStart + index * 8 + 4, true);
      if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lon) > 180 || Math.abs(lat) > 90) throw geometryError();
      coordinates.push([lon, lat]);
    }
    result.push({ id, coordinates, roadClass });
  }
  return result;
}

async function loadGeometry(signal?: AbortSignal): Promise<StreetAirSegment[]> {
  signal?.throwIfAborted();
  if (liveGeometry) return liveGeometry;
  let response: Response;
  try { response = await fetch(`${API_BASE}/api/cai/geometry`, { signal }); }
  catch (error) {
    signal?.throwIfAborted();
    throw new Error('Cannot load street colours. Check the DishAI service and try again.', { cause: error });
  }
  if (!response.ok) throw new Error(`Street colours are unavailable (HTTP ${response.status}). Try again.`);
  const length = Number(response.headers.get('content-length'));
  if (length > MAX_GEOMETRY_BYTES) throw geometryError();
  const buffer = await response.arrayBuffer();
  signal?.throwIfAborted();
  const geometry = parseStreetGeometry(buffer);
  signal?.throwIfAborted();
  liveGeometry = geometry;
  return geometry;
}

interface DemoRoad {
  properties: { kind: string; highway?: string; name?: string | null; nameEn?: string | null };
  geometry: { type: string; coordinates: unknown };
}
const demoRoads = (mapData.features as DemoRoad[]).filter((feature) => feature.properties.kind === 'road' && feature.geometry.type === 'LineString');
const demoSegments: StreetAirSegment[] = demoRoads.map((road, id) => {
  const highway = road.properties.highway ?? '';
  return { id, coordinates: road.geometry.coordinates as Coordinate[], name: road.properties.nameEn || road.properties.name || undefined,
    roadClass: ['primary', 'secondary', 'trunk'].includes(highway) ? 'major' : highway === 'tertiary' ? 'collector'
      : ['footway', 'pedestrian', 'path', 'steps'].includes(highway) ? 'path' : 'local' };
});

// Same display anchors as backend/cai/config.py. This is a product score, not an official AQI.
const scoreAnchors = [[0, 100], [5, 80], [15, 60], [25, 40], [50, 15], [100, 0]] as const;
function demoScore(pm25: number): number {
  const pm = Math.max(0, Math.min(100, pm25));
  for (let index = 1; index < scoreAnchors.length; index++) {
    const [highPm, highScore] = scoreAnchors[index];
    if (pm <= highPm) {
      const [lowPm, lowScore] = scoreAnchors[index - 1];
      return Math.round(lowScore + (highScore - lowScore) * (pm - lowPm) / (highPm - lowPm));
    }
  }
  return 0;
}

/** Geometry is lazy; hourly values are already part of the immutable forecast snapshot. */
export async function loadStreetAir(forecast: ForecastData, signal?: AbortSignal): Promise<StreetAirData> {
  signal?.throwIfAborted();
  const cached = overlayCache.get(forecast);
  if (cached) return cached;
  if (forecast.hours.length !== 24) throw new Error('Refresh the complete 24-hour forecast before showing street colours.');
  let result: StreetAirData;
  if (forecast.mode === 'demo') {
    result = { mode: forecast.mode, forecastId: forecast.id, segments: demoSegments,
      hours: forecast.hours.map((hour) => {
        if (hour.pm25 === null || !Number.isFinite(hour.pm25) || hour.pm25 < 0) throw new Error('Street colours are unavailable for this demo forecast.');
        const pm25 = hour.pm25;
        return { time: hour.time, backendHour: hour.backendHour,
          scores: Uint8Array.from(demoRoads, (road) => demoScore(pm25 * (exposureFactors[road.properties.highway ?? ''] ?? 1.1) / 1.5)) };
      }),
      note: 'Illustrative street scores use the same sample exposure factors as the demo routes. Higher scores indicate lower estimated exposure. These are not measurements or an official air-quality index.' };
  } else {
    if (!forecast.streetValues || forecast.streetValues.length !== 24) throw new Error('Street scores are missing from this forecast. Refresh the forecast to load street colours.');
    const segments = await loadGeometry(signal);
    signal?.throwIfAborted();
    if (forecast.streetValues.some((scores) => scores.length !== segments.length || scores.some((score) => score > 100))) {
      throw new Error('Street scores do not match the map. Refresh the forecast and try again.');
    }
    result = { mode: forecast.mode, forecastId: forecast.id, segments,
      hours: forecast.hours.map((hour, index) => ({ time: hour.time, backendHour: hour.backendHour, scores: forecast.streetValues![index] })),
      note: 'AirPath street score combines modelled air quality and heat exposure; higher is better. It is indicative, not an official AQI or a PM2.5 concentration. Sensor dots show snapshot readings and do not change with departure time.' };
  }
  signal?.throwIfAborted();
  overlayCache.set(forecast, result);
  return result;
}
