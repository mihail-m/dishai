import { beforeEach, describe, expect, it, vi } from 'vitest';

function wireForecast(id = 'street-snapshot', scores = [80, 50]) {
  const hours = Array.from({ length: 24 }, (_, index) => ({
    time: new Date(Date.UTC(2026, 9, 18, 9 + index)).toISOString().slice(0, 16),
    pm25_model: 14, pm25: 20, t2m: 18, wind10m: 4, cloud: 10, is_night: false,
  }));
  return { id, hours: hours.map((_, index) => 33 + index),
    forecast: { hours, generated_at: '2026-10-18T09:00', source: 'test', note: '' },
    values: hours.map((hour, index) => ({ time: hour.time, hour: 33 + index,
      cai_base64: btoa(String.fromCharCode(...scores.map((score) => score - index))) })), sensors: { sensors: [] } };
}

function binaryGeometry() {
  // Two segments, four lon/lat points, classes major and path.
  const buffer = new ArrayBuffer(8 + 12 + 32 + 2);
  const view = new DataView(buffer);
  [2, 4, 0, 2, 4].forEach((value, index) => view.setUint32(index * 4, value, true));
  [23.32, 42.69, 23.321, 42.691, 23.33, 42.70, 23.331, 42.701].forEach((value, index) => view.setFloat32(20 + index * 4, value, true));
  view.setUint8(52, 0); view.setUint8(53, 3);
  return buffer;
}

beforeEach(() => { vi.resetModules(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('street-air forecast layers', () => {
  it('loads the offline street layer without requests and preserves forecast-dependent scores and fixed sensor readings', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const { loadForecast } = await import('./data');
    const { loadStreetAir, DEMO_SENSORS } = await import('./map-air');
    const forecast = await loadForecast({ mode: 'demo' });
    const sensors = structuredClone(DEMO_SENSORS);
    const result = await loadStreetAir(forecast);
    expect(result.hours).toHaveLength(24);
    expect(result.segments.length).toBeGreaterThan(100);
    expect(result.hours.every((hour) => hour.scores.length === result.segments.length)).toBe(true);
    expect(result.hours[6].scores.every((score, index) => score > result.hours[0].scores[index])).toBe(true);
    expect(result.note).toContain('Illustrative');
    expect(result.forecastId).toBe(forecast.id);
    expect(await loadStreetAir(forecast)).toBe(result);
    expect(DEMO_SENSORS).toEqual(sensors);
    expect(DEMO_SENSORS.every((sensor) => sensor.timestamp && sensor.name.includes('sample'))).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('reuses one geometry download for all hours and different immutable forecast snapshots', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(binaryGeometry())); vi.stubGlobal('fetch', fetchMock);
    const { parseForecastWindow } = await import('./data');
    const { loadStreetAir } = await import('./map-air');
    const first = parseForecastWindow(wireForecast());
    const result = await loadStreetAir(first);
    expect(result.hours[0].scores).toEqual(Uint8Array.from([80, 50]));
    expect(result.hours[23].scores).toEqual(Uint8Array.from([57, 27]));
    expect(result.hours[23].backendHour).toBe(56);
    expect(result.segments[0].coordinates[0][0]).toBeCloseTo(23.32, 4);
    expect(result.segments[0].coordinates[0][1]).toBeCloseTo(42.69, 4);
    const refreshed = await loadStreetAir(parseForecastWindow(wireForecast('new-snapshot', [70, 60])));
    expect(refreshed.forecastId).toBe('new-snapshot');
    expect(refreshed.hours[0].scores).toEqual(Uint8Array.from([70, 60]));
    expect(result.hours[0].scores).toEqual(Uint8Array.from([80, 50]));
    expect(refreshed.segments).toBe(result.segments);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/cai/geometry');
  });

  it('does not replace invalid or missing street data with demo values or prevent the forecast loading', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    const { parseForecastWindow } = await import('./data');
    const { loadStreetAir } = await import('./map-air');
    for (const invalid of ['', 'not valid base64', btoa(String.fromCharCode(80, 255)), btoa(String.fromCharCode(80))]) {
      const wire = wireForecast(); wire.values[8].cai_base64 = invalid;
      const forecast = parseForecastWindow(wire);
      expect(forecast.hours).toHaveLength(24);
      expect(forecast.streetValues).toBeUndefined();
      await expect(loadStreetAir(forecast)).rejects.toThrow('Street scores are missing');
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects geometry that cannot be matched to its snapshot values', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(binaryGeometry())));
    const { parseForecastWindow } = await import('./data');
    const { loadStreetAir } = await import('./map-air');
    await expect(loadStreetAir(parseForecastWindow(wireForecast('mismatch', [80])))).rejects.toThrow('do not match the map');
  });

  it('shows service failures without caching an error or silently substituting demo streets', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(new Response(binaryGeometry())); vi.stubGlobal('fetch', fetchMock);
    const { parseForecastWindow } = await import('./data');
    const { loadStreetAir } = await import('./map-air');
    const forecast = parseForecastWindow(wireForecast());
    await expect(loadStreetAir(forecast)).rejects.toThrow('HTTP 503');
    expect((await loadStreetAir(forecast)).mode).toBe('live');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('honors an abort during geometry download and does not publish the interrupted snapshot', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockImplementationOnce(async (_url, options) => {
      expect(options.signal).toBe(controller.signal);
      controller.abort(); return new Response(binaryGeometry());
    }).mockResolvedValueOnce(new Response(binaryGeometry())); vi.stubGlobal('fetch', fetchMock);
    const { parseForecastWindow } = await import('./data');
    const { loadStreetAir } = await import('./map-air');
    const forecast = parseForecastWindow(wireForecast());
    await expect(loadStreetAir(forecast, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect((await loadStreetAir(forecast)).forecastId).toBe(forecast.id);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('street binary validation', () => {
  it('rejects truncated payloads, broken offsets, invalid coordinates and unknown classes before rendering', async () => {
    const { parseStreetGeometry } = await import('./map-air');
    expect(parseStreetGeometry(binaryGeometry()).map((segment) => segment.roadClass)).toEqual(['major', 'path']);
    expect(() => parseStreetGeometry(binaryGeometry().slice(0, 53))).toThrow('incomplete');
    const offset = binaryGeometry(); new DataView(offset).setUint32(12, 5, true);
    expect(() => parseStreetGeometry(offset)).toThrow('incomplete');
    const coordinate = binaryGeometry(); new DataView(coordinate).setFloat32(20, NaN, true);
    expect(() => parseStreetGeometry(coordinate)).toThrow('incomplete');
    const roadClass = binaryGeometry(); new DataView(roadClass).setUint8(52, 4);
    expect(() => parseStreetGeometry(roadClass)).toThrow('incomplete');
  });
});
