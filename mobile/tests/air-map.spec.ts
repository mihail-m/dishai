import { test, expect, type Page } from '@playwright/test';

let pageErrors: string[] = [];
test.beforeEach(async ({ page }) => { pageErrors = []; page.on('pageerror', error => pageErrors.push(error.message)); });
test.afterEach(() => { expect(pageErrors, 'The time wheel and optional layers must not throw').toEqual([]); });

async function openRoutes(page: Page) {
  await page.getByRole('button', { name: 'Routes', exact: true }).click();
  await expect(page.getByRole('slider', { name: 'Route departure hour' })).toBeEnabled();
  await expect(page.locator('.route-map-route')).toHaveCount(3);
}

async function enterConnected(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'App settings' }).click();
  await page.getByRole('button', { name: /Connect to AirPath/ }).click();
  await expect(page.locator('.data-chip')).toHaveText('Connected');
  await expect(page.getByRole('slider', { name: 'Forecast hour', exact: true })).toBeEnabled();
  await openRoutes(page);
}

function forecastTime(index: number) {
  return new Date(Date.UTC(2026, 9, 18, 9 + index)).toISOString().slice(0, 16);
}

/** The actual web-map binary contract: header, point offsets, Float32 lon/lat pairs, road classes. */
function streetGeometry() {
  const lines = [
    [[23.319, 42.698], [23.330, 42.694]],
    [[23.321, 42.695], [23.328, 42.696]],
    [[23.322, 42.697], [23.331, 42.692]],
  ];
  const pointCount = lines.reduce((sum, line) => sum + line.length, 0);
  const coordinatesStart = 8 + (lines.length + 1) * 4;
  const classesStart = coordinatesStart + pointCount * 8;
  const buffer = Buffer.alloc(classesStart + lines.length);
  buffer.writeUInt32LE(lines.length, 0);
  buffer.writeUInt32LE(pointCount, 4);
  let pointIndex = 0;
  lines.forEach((line, index) => {
    buffer.writeUInt32LE(pointIndex, 8 + index * 4);
    line.forEach(([lon, lat]) => {
      buffer.writeFloatLE(lon, coordinatesStart + pointIndex * 8);
      buffer.writeFloatLE(lat, coordinatesStart + pointIndex * 8 + 4);
      pointIndex++;
    });
    buffer.writeUInt8(0, classesStart + index);
  });
  buffer.writeUInt32LE(pointCount, 8 + lines.length * 4);
  return buffer;
}

async function mockConnectedMap(page: Page, malformedGeometry = false) {
  const calls = { forecast: 0, geometry: 0, routes: 0, routeSnapshots: [] as string[] };
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8n8AAAAASUVORK5CYII=', 'base64');
  await page.route('https://*.tile.openstreetmap.org/**', route => route.fulfill({ contentType: 'image/png', body: png }));
  await page.route('**/api/forecast/*', route => {
    calls.forecast++;
    const revision = calls.forecast;
    return route.fulfill({ json: {
      id: `air-map-${revision}`,
      hours: Array.from({ length: 24 }, (_, index) => 30 + index),
      values: Array.from({ length: 24 }, (_, index) => ({ hour: 30 + index, time: forecastTime(index),
        cai_base64: Buffer.from([20 + 3 * index + revision - 1, 90 - 2 * index, 50]).toString('base64') })),
      forecast: { generated_at: forecastTime(0), source: 'fixture', note: 'Deterministic browser fixture.',
        hours: Array.from({ length: 24 }, (_, index) => ({ time: forecastTime(index), pm25_model: 12 + index, t2m: 18, wind10m: 4, cloud: 10, is_night: false })) },
      // The community-sensor provider uses a space separator and no timezone.
      sensors: { sensors: [{ id: 'sensor-test', name: 'Central observation', lat: 42.696, lon: 23.324, pm25: 16.4, timestamp: '2026-10-18 08:45:00' }] },
    } });
  });
  await page.route('**/api/cai/geometry', route => {
    calls.geometry++;
    return route.fulfill({ contentType: 'application/octet-stream', body: malformedGeometry ? Buffer.from([1, 2, 3]) : streetGeometry() });
  });
  await page.route('**/api/route?*', route => {
    calls.routes++;
    const query = new URL(route.request().url()).searchParams;
    calls.routeSnapshots.push(query.get('forecast_id') ?? '');
    const hour = Number(query.get('hour'));
    const index = hour - 30;
    const from = query.get('from')!.split(',').map(Number).reverse();
    const to = query.get('to')!.split(',').map(Number).reverse();
    const base = { time_min: 27, distance_m: 2200, mean_pm25: 5 + Math.abs(index - 6), dose_change_pct: -16,
      polyline: [from, [23.329, 42.693], [23.324, 42.697], to] };
    return route.fulfill({ json: { hour, time: forecastTime(index), routes: {
      fastest: { ...base, time_min: 24, dose_change_pct: 0, mean_pm25: base.mean_pm25 + 5 },
      balanced: { ...base, time_min: 26, dose_change_pct: -8, mean_pm25: base.mean_pm25 + 2 },
      cleanest: base,
    }, confidence: { cleaner_exists: true }, note: 'Fixture route outlook.' } });
  });
  return calls;
}

test('departure wheel stays visible, selects every cached hour, and survives expanded map and address edits', async ({ page }) => {
  const apiCalls: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/')) apiCalls.push(request.url()); });
  await page.goto('/');
  await openRoutes(page);
  const wheel = page.getByRole('region', { name: 'Choose a departure time' });
  const slider = page.getByRole('slider', { name: 'Route departure hour' });
  await expect(wheel).toBeInViewport({ ratio: 1 });
  expect((await wheel.boundingBox())!.height).toBeLessThanOrEqual(130);
  await expect(page.locator('.departure-wheel-hour')).toHaveCount(24);
  const readings = new Set<string>();
  for (let hour = 0; hour < 24; hour++) {
    await slider.fill(String(hour));
    await expect(slider).toHaveValue(String(hour));
    await expect(page.locator('.departure-wheel-current strong')).toHaveText(forecastTime(hour).slice(11, 16));
    await expect(page.locator('.departure-wheel-hour.is-selected')).toHaveAttribute('aria-pressed', 'true');
    readings.add(await page.locator('.departure-wheel-heading p').innerText());
  }
  expect(readings.size).toBeGreaterThan(12);
  await expect(page.locator('.departure-wheel-current')).toContainText('Tomorrow');
  await expect(slider).toHaveAttribute('aria-valuetext', /Tomorrow, 08:00/);
  await slider.focus();
  await page.keyboard.press('Home');
  await expect(slider).toHaveValue('0');
  await page.locator('.departure-wheel-scroll').hover();
  await page.mouse.wheel(144, 0);
  await expect(slider).toHaveValue('3');
  await page.getByRole('button', { name: 'Next departure hour' }).click();
  await expect(slider).toHaveValue('4');
  await page.getByRole('button', { name: /^Select lowest route PM2.5/ }).click();
  await expect(slider).toHaveValue('6');
  await page.getByRole('button', { name: 'Expand map', exact: true }).click();
  await expect(wheel).toBeInViewport({ ratio: 1 });
  await slider.fill('23');
  await expect(page.locator('.route-time-row')).toContainText('Tomorrow · 08:00');
  await page.getByRole('button', { name: 'View route comparison' }).click();
  await page.getByRole('button', { name: /^Change destination address:/ }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('searchbox', { name: 'Place or street' }).fill('City Garden');
  await dialog.getByRole('button', { name: /^City Garden/ }).click();
  await expect(slider).toBeEnabled();
  await expect(slider).toHaveValue('23');
  await expect(wheel).toBeInViewport({ ratio: 1 });
  await expect(page.getByRole('button', { name: 'Change destination address: City Garden' })).toBeVisible();
  expect(apiCalls).toEqual([]);
});

test('offline street colors and sensor observations are separate opt-in layers', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1:5181')) requests.push(request.url()); });
  await page.goto('/');
  await openRoutes(page);
  const streets = page.getByRole('button', { name: 'Street air quality colors' });
  const sensors = page.getByRole('button', { name: 'Air quality sensors' });
  const slider = page.getByRole('slider', { name: 'Route departure hour' });
  await expect(streets).toHaveAttribute('aria-pressed', 'false');
  await expect(sensors).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.street-air-segment')).toHaveCount(0);
  await expect(page.locator('.air-sensor-marker')).toHaveCount(0);
  await streets.click();
  await expect(page.locator('.street-air-segment').first()).toBeAttached();
  await expect(page.locator('.air-sensor-marker')).toHaveCount(0);
  const id = await page.locator('.street-air-segment').first().getAttribute('data-street-id');
  const segment = page.locator(`.street-air-segment[data-street-id="${id}"]`);
  const beforeColor = await segment.getAttribute('stroke');
  await slider.fill('6');
  await expect(segment).not.toHaveAttribute('stroke', beforeColor!);
  await expect(segment).toHaveAttribute('data-forecast-time', '2026-10-18T15:00:00+03:00');
  await sensors.click();
  await expect(page.locator('.air-sensor-marker')).toHaveCount(6);
  const originalReadings = await page.locator('.air-sensor-marker').evaluateAll(markers => markers.map(marker => ({ label: marker.getAttribute('aria-label'), value: marker.getAttribute('data-pm25') })));
  expect(originalReadings.every(reading => reading.label?.includes('Sample sensor') && reading.label.includes('2026-10-18 · 09:00'))).toBe(true);
  await slider.fill('23');
  const laterReadings = await page.locator('.air-sensor-marker').evaluateAll(markers => markers.map(marker => ({ label: marker.getAttribute('aria-label'), value: marker.getAttribute('data-pm25') })));
  expect(laterReadings).toEqual(originalReadings);
  await streets.click();
  await expect(page.locator('.street-air-segment')).toHaveCount(0);
  await expect(page.locator('.air-sensor-marker')).toHaveCount(6);
  await sensors.click();
  await expect(page.locator('.air-sensor-marker')).toHaveCount(0);
  await expect(page.locator('.route-map-route')).toHaveCount(3);
  await expect(page.locator('.map-air-legend')).toHaveCount(0);
  expect(requests).toEqual([]);
});

test('connected map uses snapshot street scores, loads geometry once, and keeps sensor time independent', async ({ page }) => {
  const calls = await mockConnectedMap(page);
  await enterConnected(page);
  const slider = page.getByRole('slider', { name: 'Route departure hour' });
  const streets = page.getByRole('button', { name: 'Street air quality colors' });
  const sensors = page.getByRole('button', { name: 'Air quality sensors' });
  expect(calls.forecast).toBe(1);
  expect(calls.routes).toBe(24);
  expect(calls.geometry).toBe(0);
  expect(new Set(calls.routeSnapshots)).toEqual(new Set(['air-map-1']));
  await expect(page.locator('.departure-wheel-heading p')).toContainText('11.0');
  await expect(page.locator('.departure-wheel-heading p')).toContainText('Route PM2.5 estimate');
  await sensors.click();
  const sensor = page.locator('.air-sensor-marker');
  await expect(sensor).toHaveCount(1);
  await expect(sensor).toHaveAttribute('data-pm25', '16.4');
  await expect(sensor).toHaveAttribute('aria-label', /Central observation.*08:45/);
  const originalSensor = await sensor.getAttribute('aria-label');
  expect(calls.geometry).toBe(0);
  await streets.click();
  const segment = page.locator('.street-air-segment[data-street-id="0"]');
  await expect(segment).toHaveAttribute('data-street-score', '20');
  const color = await segment.getAttribute('stroke');
  expect(calls.geometry).toBe(1);
  await slider.fill('23');
  await expect(segment).toHaveAttribute('data-street-score', '89');
  await expect(segment).toHaveAttribute('data-forecast-time', forecastTime(23));
  await expect(segment).not.toHaveAttribute('stroke', color!);
  await expect(sensor).toHaveAttribute('aria-label', originalSensor!);
  await expect(sensor).toHaveAttribute('data-pm25', '16.4');
  await expect(page.locator('.departure-wheel-heading p')).toContainText('22.0');
  expect({ forecast: calls.forecast, geometry: calls.geometry, routes: calls.routes }).toEqual({ forecast: 1, geometry: 1, routes: 24 });
  await streets.click();
  await expect(segment).toHaveCount(0);
  await streets.click();
  await expect(segment).toHaveAttribute('data-street-score', '89');
  expect(calls.geometry).toBe(1);
  await page.getByRole('button', { name: 'Refresh 24-hour forecast' }).click();
  await expect(slider).toBeEnabled();
  await expect(segment).toHaveAttribute('data-street-score', '90');
  await expect(slider).toHaveValue('23');
  await expect.poll(() => calls.routes).toBe(48);
  expect(calls.forecast).toBe(2);
  expect(calls.geometry).toBe(1);
  expect(new Set(calls.routeSnapshots.slice(24))).toEqual(new Set(['air-map-2']));
  await page.getByRole('button', { name: /^Select lowest route PM2.5/ }).click();
  await expect(slider).toHaveValue('6');
  await expect(segment).toHaveAttribute('data-street-score', '39');
  expect(calls.routes).toBe(48);
});

test('malformed optional street geometry shows an error while routes, sensors and departures still work', async ({ page }) => {
  const calls = await mockConnectedMap(page, true);
  await enterConnected(page);
  await page.getByRole('button', { name: 'Street air quality colors' }).click();
  await expect(page.locator('.map-layer-error')).toContainText('street map data is incomplete or unsupported');
  await expect(page.locator('.street-air-segment')).toHaveCount(0);
  await expect(page.locator('.route-map-route')).toHaveCount(3);
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('18');
  await expect(page.locator('.departure-wheel-current')).toContainText('Tomorrow');
  await page.getByRole('button', { name: 'Air quality sensors' }).click();
  await expect(page.locator('.air-sensor-marker')).toHaveCount(1);
  await page.getByRole('button', { name: 'Retry street colors' }).click();
  await expect.poll(() => calls.geometry).toBe(2);
  await expect(page.locator('.map-layer-error')).toBeVisible();
  await page.getByRole('button', { name: 'Street air quality colors' }).click();
  await expect(page.locator('.map-layer-error')).toHaveCount(0);
  await page.getByRole('button', { name: 'Expand map', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Choose a departure time' })).toBeInViewport({ ratio: 1 });
  await expect(page.locator('.route-map-route')).toHaveCount(3);
  expect(calls.routes).toBe(24);
});

test('street and sensor clicks pick addresses while normal observation popups and remounts remain usable', async ({ page }) => {
  await mockConnectedMap(page);
  await enterConnected(page);
  await page.getByRole('button', { name: 'Street air quality colors' }).click();
  await page.getByRole('button', { name: 'Air quality sensors' }).click();
  await expect(page.locator('.street-air-segment')).toHaveCount(3);
  await expect(page.locator('.air-sensor-marker')).toHaveCount(1);
  await page.getByRole('button', { name: 'Expand map', exact: true }).click();

  async function clickVisiblePath(selector: string) {
    // Exercise the real hit target, including overlapping route lines and cards.
    const point = await page.locator(selector).evaluateAll(elements => {
      for (const element of elements) {
        const path = element as SVGPathElement;
        const matrix = path.getScreenCTM();
        if (!matrix) continue;
        for (const fraction of [.5, .25, .75, .1, .9]) {
          const local = path.getPointAtLength(path.getTotalLength() * fraction);
          const screen = new DOMPoint(local.x, local.y).matrixTransform(matrix);
          if (document.elementFromPoint(screen.x, screen.y) === element) return { x: screen.x, y: screen.y };
        }
      }
      return null;
    });
    expect(point, `${selector} must be reachable on the map`).not.toBeNull();
    await page.mouse.click(point!.x, point!.y);
  }

  await clickVisiblePath('.air-sensor-marker');
  await expect(page.locator('.map-air-popup')).toContainText('Central observation');
  await expect(page.locator('.map-air-popup')).toContainText('Reading 2026-10-18 · 08:45');
  await expect(page.locator('.map-air-popup')).not.toContainText('unavailable');
  await page.locator('.leaflet-popup-close-button').click();

  for (const [endpoint, selector] of [['destination', '.street-air-segment'], ['starting', '.air-sensor-marker']]) {
    const address = page.getByRole('button', { name: new RegExp(`^Change ${endpoint} address:`) });
    const previous = await address.getAttribute('aria-label');
    await address.click();
    await page.getByRole('button', { name: /Choose on map/ }).click();
    await expect(page.locator('.map-pick-card')).toBeVisible();
    await clickVisiblePath(selector);
    await expect(page.locator('.map-pick-card')).toHaveCount(0);
    await expect(page.locator('.map-air-popup')).toHaveCount(0);
    await expect(page.getByRole('slider', { name: 'Route departure hour' })).toBeEnabled();
    await expect(address).not.toHaveAttribute('aria-label', previous!);
  }

  await page.getByRole('button', { name: 'Today', exact: true }).click();
  await openRoutes(page);
  await expect(page.getByRole('button', { name: 'Street air quality colors' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: 'Air quality sensors' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.street-air-segment').first()).toBeAttached();
  await expect(page.locator('.air-sensor-marker')).toHaveCount(1);
});
