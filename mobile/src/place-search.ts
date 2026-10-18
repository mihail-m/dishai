import type { Place } from './types';

const cache = new Map<string, Place[]>();
let nextRequestAt = 0;
let queue: Promise<unknown> = Promise.resolve();

/** Optional Nominatim-compatible provider; the demo never depends on a public geocoder. */
export function getGeocoderUrl(): string | null {
  let runtimeUrl: string | null = null;
  try { runtimeUrl = localStorage.getItem('airpath-geocoder-url'); } catch { /* Storage can be disabled. */ }
  const value = runtimeUrl ?? import.meta.env.VITE_GEOCODER_URL;
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value, globalThis.location?.origin ?? 'http://localhost');
    return ['http:', 'https:'].includes(url.protocol) ? url.toString() : null;
  } catch { return null; }
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal?.reason); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, milliseconds);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

function parsePlaces(payload: unknown): Place[] {
  if (!Array.isArray(payload)) throw new Error('Address search returned an unexpected response. Try choosing a point on the map.');
  const unique = new Set<string>();
  return payload.flatMap((item: unknown): Place[] => {
    if (!item || typeof item !== 'object') return [];
    const value = item as Record<string, unknown>;
    const lon = Number(value.lon); const lat = Number(value.lat);
    if (!Number.isFinite(lon) || !Number.isFinite(lat) || lon < 23.20 || lon > 23.45 || lat < 42.60 || lat > 42.80
      || typeof value.display_name !== 'string' || !value.display_name.trim()) return [];
    const key = `${lon.toFixed(5)},${lat.toFixed(5)}`;
    if (unique.has(key)) return [];
    unique.add(key);
    return [{ label: value.display_name.split(',').slice(0, 3).join(',').trim(), coordinates: [lon, lat] }];
  }).slice(0, 6);
}

/** Explicit-submit only. Serialize and space requests, cache results, and respect cancellation. */
export async function searchStreetAddresses(query: string, signal?: AbortSignal): Promise<Place[]> {
  const provider = getGeocoderUrl();
  if (!provider) throw new Error('Choose a place from the list or a point on the map.');
  const normalized = query.trim().replace(/\s+/g, ' ').slice(0, 180);
  if (normalized.length < 3) return [];
  const key = `${provider}:${normalized.toLocaleLowerCase()}`;
  const request = queue.then(async () => {
    signal?.throwIfAborted();
    const cached = cache.get(key);
    if (cached) return cached;
    await wait(Math.max(0, nextRequestAt - Date.now()), signal);
    signal?.throwIfAborted();
    nextRequestAt = Date.now() + 1100;
    const url = new URL(provider);
    url.searchParams.set('format', 'jsonv2');
    url.searchParams.set('q', normalized);
    url.searchParams.set('countrycodes', 'bg');
    url.searchParams.set('viewbox', '23.20,42.80,23.45,42.60');
    url.searchParams.set('bounded', '1');
    url.searchParams.set('limit', '6');
    url.searchParams.set('accept-language', 'en,bg');
    const controller = new AbortController();
    const cancel = () => controller.abort(signal?.reason);
    signal?.addEventListener('abort', cancel, { once: true });
    const timer = setTimeout(() => controller.abort(new Error('Address search timed out. Choose a local place or try again.')), 10000);
    try {
      const response = await fetch(url, { signal: controller.signal, headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error('Address search is unavailable. Choose a local place or a point on the map.');
      const places = parsePlaces(await response.json());
      signal?.throwIfAborted();
      cache.set(key, places);
      return places;
    } catch (error) {
      if (signal?.aborted) throw signal.reason;
      if (controller.signal.aborted) throw new Error('Address search timed out. Choose a local place or try again.');
      if (error instanceof TypeError) throw new Error('Address search could not connect. Local places and map selection are still available.');
      throw error;
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
  });
  queue = request.catch(() => undefined);
  return request;
}
