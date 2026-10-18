import { beforeEach, describe, expect, it, vi } from 'vitest';

function wireForecast() {
  const hours = Array.from({ length: 30 }, (_, index) => ({
    time: new Date(Date.UTC(2026, 9, 18, 9 + index)).toISOString().slice(0, 16),
    pm25_model: 14 - index / 3, pm25: 20, t2m: 18, wind10m: 4, cloud: 10, is_night: false,
  }));
  return {
    id: 'test-snapshot', hours: hours.map((_, index) => 33 + index),
    forecast: { hours, generated_at: '2026-10-18T09:00', source: 'test service', note: '', now_index: 0 },
    values: hours.map((hour, index) => ({ time: hour.time, hour: 33 + index, cai_base64: '' })),
    sensors: { sensors: [{ id: 1, lat: 42.69, lon: 23.32, pm25: 9.1 }] },
  };
}

beforeEach(() => { vi.resetModules(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('forecast snapshot contract', () => {
  it('loads precisely 24 hours while preserving actual backend indices and sensor coordinate order', async () => {
    const { parseForecastWindow } = await import('./data');
    const result = parseForecastWindow(wireForecast());
    expect(result.hours).toHaveLength(24);
    expect(result.hours[0]).toMatchObject({ index: 0, backendHour: 33, pm25: 14, pm25Source: 'model', label: '09:00' });
    expect(result.hours[23].backendHour).toBe(56);
    expect(result.sensors[0].coordinates).toEqual([23.32, 42.69]);
  });

  it('rejects an incomplete or misaligned snapshot before publishing it', async () => {
    const { parseForecastWindow } = await import('./data');
    const short = wireForecast(); short.forecast.hours = short.forecast.hours.slice(0, 23);
    expect(() => parseForecastWindow(short)).toThrow('complete 24-hour');
    const shifted = wireForecast(); shifted.values[4].hour = 39;
    expect(() => parseForecastWindow(shifted)).toThrow('complete 24-hour');
    const timeGap = wireForecast();
    timeGap.forecast.hours[4].time = timeGap.forecast.hours[5].time;
    timeGap.values[4].time = timeGap.forecast.hours[5].time;
    expect(() => parseForecastWindow(timeGap)).toThrow('complete 24-hour');
  });

  it('preserves real zeroes and never interprets missing PM2.5 as good air', async () => {
    const { parseForecastWindow } = await import('./data');
    const wire = wireForecast();
    Object.assign(wire.forecast.hours[0], { pm25_model: 0, pm25: 20 });
    Object.assign(wire.forecast.hours[1], { pm25_model: null, pm25: null });
    Object.assign(wire.forecast.hours[2], { pm25_model: null, pm25: 7 });
    const result = parseForecastWindow(wire);
    expect(result.hours[0]).toMatchObject({ pm25: 0, pm25Source: 'model' });
    expect(result.hours[1]).toMatchObject({ pm25: null, quality: 'unavailable', pm25Source: 'unavailable' });
    expect(result.hours[2]).toMatchObject({ pm25: 7, pm25Source: 'regional' });
  });

  it('chooses a complete consecutive window and excludes missing hours', async () => {
    const { chooseBestWindow } = await import('./data');
    expect(chooseBestWindow([12, null, 1, 9, 4, 3].map((pm25) => ({ pm25 })))).toEqual({ startIndex: 4, endIndex: 5, meanPm25: 3.5 });
    expect(chooseBestWindow([{ pm25: null }, { pm25: 0 }])).toBeNull();
    expect(chooseBestWindow([{ pm25: 1 }], 0)).toBeNull();
  });

  it('calculates window end times across midnight without using the device timezone', async () => {
    const { windowEndTime } = await import('./data');
    expect(windowEndTime('2026-10-18T23:00')).toBe('2026-10-19T00:00');
    expect(windowEndTime('2026-12-31T23:00:00+02:00')).toBe('2027-01-01T00:00:00+02:00');
    expect(windowEndTime('2026-10-18T14:00:00+03:00')).toBe('2026-10-18T15:00:00+03:00');
  });

  it('refreshes live data with POST and preserves the last good forecast after a failed refresh', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(wireForecast()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Feed unavailable' }), { status: 503 }));
    vi.stubGlobal('fetch', fetchMock);
    const { loadForecast } = await import('./data');
    const initial = await loadForecast({ mode: 'live' });
    await expect(loadForecast({ mode: 'live', force: true })).rejects.toThrow('Feed unavailable');
    expect(await loadForecast({ mode: 'live' })).toBe(initial);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toContain('/api/forecast/refresh');
    expect(fetchMock.mock.calls[1][1].method).toBe('POST');
  });

  it('requests a fresh forecast when returning to connected mode after the cache expires', async () => {
    let now = Date.UTC(2026, 9, 18, 6);
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    const first = wireForecast(), second = { ...wireForecast(), id: 'new-snapshot' };
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(first)))
      .mockResolvedValueOnce(new Response(JSON.stringify(second)));
    vi.stubGlobal('fetch', fetchMock);
    const { loadForecast } = await import('./data');
    const initial = await loadForecast({ mode: 'live' });
    now += 4 * 60 * 1000;
    expect(await loadForecast({ mode: 'live' })).toBe(initial);
    now += 60 * 1000;
    expect((await loadForecast({ mode: 'live' })).id).toBe('new-snapshot');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('route data', () => {
  it('preloads deterministic demo routes offline, with real street geometry and reversible endpoints', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const { loadForecast, preloadRoutes, loadRoutes, DEMO_TRIP } = await import('./data');
    const forecast = await loadForecast({ mode: 'demo' });
    const onProgress = vi.fn();
    const routes = await preloadRoutes({ forecast, onProgress });
    expect(routes).toHaveLength(24);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(routes[0].routes.fast.polyline.length).toBeGreaterThan(20);
    expect(routes[0].routes.fast.distanceKm).toBeGreaterThan(2);
    expect(Object.keys(routes[0].routes)).toEqual(['fast', 'balanced', 'clean']);
    expect(new Set(Object.values(routes[0].routes).map((option) => JSON.stringify(option.polyline))).size).toBe(3);
    expect(routes[0].routes.clean.polyline[0]).toEqual(DEMO_TRIP.from.coordinates);
    expect(routes[0].confidence.cleanerExists).toBeNull();
    expect(routes[0].note).toContain('illustrative');
    for (const { routes: options } of routes) {
      expect(options.clean.distanceKm).toBeGreaterThan(options.balanced.distanceKm);
      expect(options.balanced.distanceKm).toBeGreaterThanOrEqual(options.fast.distanceKm);
      expect(options.clean.doseChangePct).toBeLessThan(options.balanced.doseChangePct);
      expect(options.balanced.doseChangePct).toBeLessThan(0);
      for (const option of [options.balanced, options.clean]) {
        const calculated = 100 * (option.meanPm25 * option.minutes / (options.fast.meanPm25 * options.fast.minutes) - 1);
        expect(option.doseChangePct).toBeCloseTo(calculated, 1);
      }
    }
    expect(onProgress).toHaveBeenLastCalledWith(24, 24);
    const reversed = await loadRoutes({ mode: 'demo', hour: 0, reversed: true });
    expect(reversed.from).toEqual(DEMO_TRIP.to);
    expect(reversed.routes.clean.polyline[0]).toEqual(DEMO_TRIP.to.coordinates);
  });

  it('routes changed endpoints over actual connected OSM segments and keeps their forecast caches separate', async () => {
    const { loadForecast, loadRoutes, preloadRoutes, resolveDemoPlace, DEMO_TRIP } = await import('./data');
    const { default: map } = await import('./data/sofia-map.json');
    const segments = new Set<string>();
    for (const feature of map.features.filter((feature) => feature.properties.kind === 'road')) {
      const points = feature.geometry.coordinates as number[][];
      for (let index = 1; index < points.length; index++) {
        segments.add(`${points[index - 1]}/${points[index]}`);
        segments.add(`${points[index]}/${points[index - 1]}`);
      }
    }
    const original = await loadRoutes({ mode: 'demo', hour: 0 });
    const plan = { from: DEMO_TRIP.from, to: resolveDemoPlace([23.3213, 42.70035], 'Central Market Hall') };
    const forecast = await loadForecast({ mode: 'demo' });
    const changed = await preloadRoutes({ forecast, plan });
    expect(changed).toHaveLength(24);
    expect(changed[0].routes.fast.polyline).not.toEqual(original.routes.fast.polyline);
    for (const hour of changed) {
      expect(hour.to).toEqual(plan.to);
      for (const option of Object.values(hour.routes)) {
        expect(option.polyline[0]).toEqual(plan.from.coordinates);
        expect(option.polyline.at(-1)).toEqual(plan.to.coordinates);
        for (let index = 1; index < option.polyline.length; index++) {
          expect(segments.has(`${option.polyline[index - 1]}/${option.polyline[index]}`)).toBe(true);
        }
      }
    }
    expect(await loadRoutes({ mode: 'demo', hour: 0 })).toBe(original);
  });

  it('searches local English and Bulgarian places offline and rejects invalid map selections', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const { searchRoutePlaces, resolveDemoPlace, loadRoutes, DEMO_TRIP } = await import('./data');
    expect(searchRoutePlaces('central market')[0].label).toBe('Central Market Hall');
    expect(searchRoutePlaces('Княжеска')[0]).toEqual(DEMO_TRIP.from);
    expect(searchRoutePlaces('Pirotska')).toContainEqual(DEMO_TRIP.to);
    expect(resolveDemoPlace([23.3149941, 42.7004183]).label).not.toBe('Selected location');
    expect(() => resolveDemoPlace([23.4, 42.8])).toThrow('central Sofia');
    await expect(loadRoutes({ mode: 'demo', hour: 0, plan: { from: DEMO_TRIP.from, to: DEMO_TRIP.from } })).rejects.toThrow('35 metres');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('stops progress callbacks after an aborted preload, including already cached routes', async () => {
    const { loadForecast, preloadRoutes } = await import('./data');
    const forecast = await loadForecast({ mode: 'demo' });
    await preloadRoutes({ forecast });
    const controller = new AbortController();
    const onProgress = vi.fn((loaded: number) => { if (loaded === 1) controller.abort(); });
    await expect(preloadRoutes({ forecast, signal: controller.signal, onProgress })).rejects.toMatchObject({ name: 'AbortError' });
    expect(onProgress.mock.calls.map(([loaded]) => loaded)).toEqual([0, 1]);
  });

  it('uses the snapshot backend hour and latitude/longitude for live routes and keeps uncertainty', async () => {
    const wire = wireForecast();
    const route = { time_min: 15, distance_m: 1170, mean_pm25: 11, dose_change_pct: -12,
      polyline: [[23.333122, 42.6915547], [23.3249083, 42.6955847]] };
    const response = { hour: 38, time: wire.forecast.hours[5].time, routes: { fastest: { ...route, dose_change_pct: 0 }, balanced: { ...route, dose_change_pct: -7 }, cleanest: route },
      confidence: { cleaner_exists: false, routes: { balanced: { distinguishable: true, diff_pct: -7 }, cleanest: { distinguishable: false, diff_pct: -12 } }, message: 'Within model uncertainty.' }, note: 'Modelled PM2.5.' };
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(wire)))
      .mockResolvedValueOnce(new Response(JSON.stringify(response)));
    vi.stubGlobal('fetch', fetchMock);
    const { loadForecast, loadRoutes } = await import('./data');
    await loadForecast({ mode: 'live' });
    const result = await loadRoutes({ mode: 'live', hour: 5, forecastId: 'test-snapshot' });
    const query = new URL(fetchMock.mock.calls[1][0], 'http://test').searchParams;
    expect(query.get('hour')).toBe('38');
    expect(query.get('from')).toBe('42.6915547,23.333122');
    expect(query.get('forecast_id')).toBe('test-snapshot');
    expect(query.get('include_google')).toBe('false');
    expect(result.routes.clean.title).toBe('Alternative route');
    expect(result.routes.balanced).toMatchObject({ title: 'Balanced', doseChangePct: -7, cleanerExists: true });
    expect(result.confidence.cleanerExists).toBe(false);
    expect(result.routes.clean.polyline[0]).toEqual([23.333122, 42.6915547]);
  });

  it('sends edited endpoints to the connected backend without snapping them to the demo graph', async () => {
    const wire = wireForecast();
    const route = { time_min: 35, distance_m: 2800, mean_pm25: 11, dose_change_pct: -12,
      polyline: [[23.34, 42.68], [23.30, 42.71]] };
    const response = { hour: 33, time: wire.forecast.hours[0].time, routes: { fastest: { ...route, dose_change_pct: 0 }, balanced: route, cleanest: route }, confidence: {} };
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(wire))).mockResolvedValueOnce(new Response(JSON.stringify(response)));
    vi.stubGlobal('fetch', fetchMock);
    const { loadRoutes } = await import('./data');
    const plan = { from: { label: 'My start', coordinates: [23.34, 42.68] as [number, number] }, to: { label: 'My destination', coordinates: [23.30, 42.71] as [number, number] } };
    const result = await loadRoutes({ mode: 'live', hour: 0, plan });
    const query = new URL(fetchMock.mock.calls[1][0], 'http://test').searchParams;
    expect(query.get('from')).toBe('42.68,23.34');
    expect(query.get('to')).toBe('42.71,23.3');
    expect(result.from).toEqual(plan.from);
    expect(result.to).toEqual(plan.to);
    expect(result.routes.balanced.cleanerExists).toBeNull();
    expect(result.routes.clean.cleanerExists).toBeNull();
  });

  it('does not replace a live service failure with demo data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')));
    const { loadForecast } = await import('./data');
    await expect(loadForecast({ mode: 'live' })).rejects.toThrow('Cannot reach');
  });
});
