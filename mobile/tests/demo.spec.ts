import { test, expect, type Page } from '@playwright/test';

let pageErrors: string[] = [];
test.beforeEach(async ({ page }) => { pageErrors = []; page.on('pageerror', error => pageErrors.push(error.message)); });
test.afterEach(() => { expect(pageErrors, 'No uncaught errors during navigation').toEqual([]); });

async function expectRoutesReady(page: Page) {
  await expect(page.getByRole('button', { name: 'Preview this route' })).toBeEnabled();
  await expect(page.locator('.route-option')).toHaveCount(3);
  for (const name of ['Fastest', 'Balanced', 'Cleanest']) await expect(page.getByRole('button', { name: new RegExp(`${name}.*min`) })).toBeEnabled();
  await expect(page.locator('.route-map-route')).toHaveCount(3);
}

async function chooseLocalPlace(page: Page, endpoint: 'starting' | 'destination', query: string, label: string) {
  await page.getByRole('button', { name: new RegExp(`^Change ${endpoint} address:`) }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('searchbox', { name: 'Place or street' }).fill(query);
  await dialog.getByRole('button', { name: new RegExp(`^${label}`) }).click();
  await expect(dialog).toHaveCount(0);
}

test('offline demo supports a complete recording journey with locally cached hours', async ({ page }) => {
  const externalRequests: string[] = [];
  page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1:5181')) externalRequests.push(request.url()); });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your day, a little clearer.' })).toBeVisible();
  await page.getByRole('slider', { name: 'Forecast hour', exact: true }).fill('6');
  await expect(page.locator('.particle-reading strong')).toHaveText('6.2');
  await expect(page.locator('.selected-time')).toContainText('15:00');
  await page.getByRole('button', { name: 'Refresh 24-hour forecast' }).click();
  await expect(page.getByRole('status')).toContainText('24 hours are ready');
  await expect(page.getByRole('slider', { name: 'Forecast hour', exact: true })).toHaveValue('6');
  await page.getByRole('button', { name: 'Routes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Preview this route' })).toBeEnabled();
  await expect(page.locator('.leaflet-container')).toBeVisible();
  await expect(page.getByRole('link', { name: '© OpenStreetMap contributors' })).toBeVisible();
  await page.getByRole('button', { name: /Fastest.*min/ }).click();
  await expect(page.locator('.exposure-insight')).toContainText('shortest time');
  await page.getByRole('button', { name: /Balanced.*min/ }).click();
  await expect(page.locator('.route-option.is-selected')).toContainText('Balanced');
  await page.getByRole('button', { name: /Cleanest.*min/ }).click();
  await expect(page.locator('.exposure-insight')).toContainText('Illustrative comparison');
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('16');
  await expect(page.locator('.route-time-row')).toContainText('Tomorrow · 01:00');
  await page.getByRole('button', { name: 'Reverse walking route' }).click();
  await expect(page.locator('.trip-line strong')).toHaveText('Prince’s Garden');
  await expect(page.getByRole('button', { name: 'Preview this route' })).toBeEnabled();
  await page.getByRole('button', { name: 'Preview this route' }).click();
  await expect(page.getByRole('button', { name: 'View route comparison' })).toBeVisible();
  await page.getByRole('button', { name: 'My day', exact: true }).click();
  await page.getByRole('button', { name: 'Explore this window' }).click();
  await expect(page.getByRole('dialog')).toContainText('14:00–16:00');
  await page.getByRole('button', { name: 'Save to my day', exact: true }).click();
  await expect(page.locator('.saved-plan')).toContainText('14:00–16:00');
  await page.reload();
  await page.getByRole('button', { name: 'My day', exact: true }).click();
  await expect(page.locator('.saved-plan')).toBeVisible();
  expect(externalRequests).toEqual([]);
});

test('connected failure is explicit and returning to demo restores usable data', async ({ page }) => {
  await page.route('**/api/forecast/window', route => route.fulfill({ status: 503, json: { detail: 'Forecast service is offline' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'App settings' }).click();
  await page.getByRole('button', { name: /Connect to AirPath/ }).click();
  await expect(page.getByRole('alert')).toContainText('Forecast service is offline');
  await expect(page.locator('.air-card')).toHaveCount(0);
  await page.getByRole('button', { name: 'Open demo', exact: true }).click();
  await expect(page.locator('.data-chip')).toHaveText('Demo');
  await expect(page.locator('.particle-reading strong')).toHaveText('14.2');
});

test('successful connected refresh preserves timestamp and cached sliders make no API calls', async ({ page }) => {
  let forecastCalls = 0;
  let routeCalls = 0;
  const routeQueries: URLSearchParams[] = [];
  let holdNewRoutes = false;
  let releaseNewRoutes = () => {};
  const pendingRoutes = new Promise<void>(resolve => { releaseNewRoutes = resolve; });
  const hours = Array.from({ length: 24 }, (_, i) => ({ time: new Date(Date.UTC(2026, 9, 18, 9 + i)).toISOString().slice(0, 16), pm25_model: 12 + i, t2m: 18, wind10m: 4, cloud: 10, is_night: false }));
  const payload = (revision: number) => ({ id: `snapshot-${revision}`, hours: hours.map((_, i) => i + 30), values: hours.map((hour, i) => ({ time: hour.time, hour: i + 30 })), forecast: { hours: hours.map(h => ({ ...h, pm25_model: h.pm25_model + revision })), generated_at: hours[0].time, source: 'fixture', note: 'Saved data.' }, sensors: { sensors: [] } });
  await page.route('**/api/forecast/*', async route => { forecastCalls++; await route.fulfill({ json: payload(forecastCalls) }); });
  await page.route('**/api/route?*', async route => {
    routeCalls++;
    const query = new URL(route.request().url()).searchParams;
    routeQueries.push(query);
    const hour = Number(query.get('hour'));
    const changedDestination = query.get('to') !== routeQueries[0].get('to');
    const from = query.get('from')!.split(',').map(Number).reverse();
    const to = query.get('to')!.split(',').map(Number).reverse();
    const option = { time_min: changedDestination ? 22 : 16, distance_m: 1200, mean_pm25: 11, dose_change_pct: -8, polyline: [from, [23.329, 42.693], to] };
    if (holdNewRoutes) await pendingRoutes;
    await route.fulfill({ json: { hour, time: hours[hour - 30].time, routes: { fastest: { ...option, dose_change_pct: 0 }, balanced: { ...option, dose_change_pct: -4 }, cleanest: option }, confidence: { cleaner_exists: false, message: 'Within uncertainty.' }, note: 'Test route' } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'App settings' }).click();
  await page.getByRole('button', { name: /Connect to AirPath/ }).click();
  await expect(page.locator('.data-footnote')).toContainText('Saved offline data');
  await page.getByRole('slider', { name: 'Forecast hour', exact: true }).fill('5');
  await expect(page.locator('.particle-reading strong')).toHaveText('18.0');
  const beforeSlider = forecastCalls;
  await page.getByRole('slider', { name: 'Forecast hour', exact: true }).fill('23');
  await page.getByRole('slider', { name: 'Forecast hour', exact: true }).fill('5');
  expect(forecastCalls).toBe(beforeSlider);
  await page.getByRole('button', { name: 'Refresh 24-hour forecast' }).click();
  await expect(page.locator('.particle-reading strong')).toHaveText('19.0');
  await expect(page.getByRole('slider', { name: 'Forecast hour', exact: true })).toHaveValue('5');
  await page.getByRole('button', { name: 'Routes', exact: true }).click();
  await expectRoutesReady(page);
  expect(routeCalls).toBe(24);
  await expect(page.getByRole('button', { name: /Cleanest.*min/ })).toBeVisible();
  await expect(page.locator('.exposure-insight')).toContainText('within model uncertainty');
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('20');
  expect(routeCalls).toBe(24);
  holdNewRoutes = true;
  await chooseLocalPlace(page, 'destination', 'City Garden', 'City Garden');
  await expect(page.getByRole('button', { name: 'Change destination address: City Garden' })).toBeVisible();
  await expect(page.locator('.route-loading')).toBeVisible();
  await expect(page.locator('.route-option')).toHaveCount(0);
  await expect(page.locator('.route-map-route')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Preview this route' })).toBeDisabled();
  holdNewRoutes = false; releaseNewRoutes();
  await expectRoutesReady(page);
  expect(routeCalls).toBe(48);
  const newTripQueries = routeQueries.slice(24);
  expect(new Set(newTripQueries.map(query => query.get('from')))).toEqual(new Set([routeQueries[0].get('from')]));
  expect(new Set(newTripQueries.map(query => query.get('to'))).size).toBe(1);
  expect(newTripQueries[0].get('to')).not.toBe(routeQueries[0].get('to'));
  expect(newTripQueries.map(query => Number(query.get('hour'))).sort((a, b) => a - b)).toEqual(Array.from({ length: 24 }, (_, index) => index + 30));
  await expect(page.locator('.route-option--clean strong')).toContainText('22');
  await expect(page.getByRole('slider', { name: 'Route departure hour' })).toHaveValue('20');
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('7');
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('21');
  expect(routeCalls).toBe(48);
});

test('local address editing, reversal, reset and map picking update all three routes offline', async ({ page }) => {
  const externalRequests: string[] = [];
  page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1:5181')) externalRequests.push(request.url()); });
  await page.goto('/');
  await page.getByRole('button', { name: 'Routes', exact: true }).click();
  await expectRoutesReady(page);
  await expect(page.getByRole('button', { name: 'Change destination address: Pirotska Street' })).toBeVisible();
  const originalPath = await page.locator('.route-map-route--selected').getAttribute('d');
  const originalDistance = await page.locator('.route-option--fast .route-option-distance').innerText();
  expect(Number.parseFloat(originalDistance)).toBeGreaterThan(1.5);

  await chooseLocalPlace(page, 'destination', 'Prince', 'Prince’s Garden');
  await expect(page.getByRole('status')).toContainText('Choose a different starting point');
  await expect(page.getByRole('button', { name: 'Change destination address: Pirotska Street' })).toBeVisible();

  await chooseLocalPlace(page, 'destination', 'City Garden', 'City Garden');
  await expectRoutesReady(page);
  await expect(page.locator('.route-map-route--selected')).not.toHaveAttribute('d', originalPath!);
  await chooseLocalPlace(page, 'starting', 'Vitosha', 'Vitosha Boulevard');
  await expectRoutesReady(page);
  await expect(page.getByRole('button', { name: 'Change starting address: Vitosha Boulevard' })).toBeVisible();
  await page.getByRole('button', { name: 'Reverse walking route' }).click();
  await expectRoutesReady(page);
  await expect(page.getByRole('button', { name: 'Change starting address: City Garden' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Change destination address: Vitosha Boulevard' })).toBeVisible();
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('23');
  await expectRoutesReady(page);
  await expect(page.locator('.route-time-row')).toContainText('Tomorrow · 08:00');

  await page.getByRole('button', { name: 'Reset to the longer demo trip' }).click();
  await expectRoutesReady(page);
  await expect(page.getByRole('button', { name: 'Change starting address: Prince’s Garden' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Change destination address: Pirotska Street' })).toBeVisible();
  await expect(page.locator('.route-option--fast .route-option-distance')).toHaveText(originalDistance);
  await page.getByRole('button', { name: 'Change destination address: Pirotska Street' }).click();
  await page.getByRole('button', { name: /Choose on map/ }).click();
  await expect(page.locator('.map-pick-card')).toContainText('Choose your destination');

  // Pick a point on the actual rendered route, not a hard-coded screen position.
  const point = await page.locator('.route-map-route--selected').evaluate(element => {
    const path = element as SVGPathElement;
    const matrix = path.getScreenCTM();
    if (!matrix) return null;
    for (const fraction of [.5, .4, .6, .3, .7, .2, .8]) {
      const local = path.getPointAtLength(path.getTotalLength() * fraction);
      const screen = new DOMPoint(local.x, local.y).matrixTransform(matrix);
      const target = document.elementFromPoint(screen.x, screen.y);
      if (target?.closest('.leaflet-route-pane')) return { x: screen.x, y: screen.y };
    }
    return null;
  });
  expect(point, 'A road segment must remain reachable while selecting an address').not.toBeNull();
  await page.mouse.click(point!.x, point!.y);
  await expect(page.locator('.map-pick-card')).toHaveCount(0);
  await expectRoutesReady(page);
  await expect(page.getByRole('button', { name: /^Change destination address:/ })).not.toHaveAttribute('aria-label', 'Change destination address: Pirotska Street');
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('4');
  await expectRoutesReady(page);
  expect(externalRequests).toEqual([]);
});

test('small screens and reduced motion retain reachable navigation and dialogs', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Understand air quality' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  for (const name of ['Routes', 'My day', 'Progress', 'Today']) {
    await page.getByRole('button', { name, exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const nav = await page.locator('.glass-tabbar').boundingBox();
    expect(nav!.y + nav!.height).toBeLessThanOrEqual(740);
  }
  await page.getByRole('button', { name: 'Routes', exact: true }).click();
  await page.getByRole('button', { name: /^Change destination address:/ }).click();
  await expect(page.getByRole('searchbox', { name: 'Place or street' })).toBeVisible();
  await page.getByRole('searchbox', { name: 'Place or street' }).fill('No matching street');
  await expect(page.getByRole('dialog')).toContainText('No local match');
  await expect(page.getByRole('button', { name: /Choose on map/ })).toBeInViewport();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('guided presentation takes the user through all six demo moments', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'App settings' }).click();
  await page.getByRole('button', { name: 'Take the demo tour' }).click();
  await expect(page.locator('.demo-director')).toContainText('1/6');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.getByRole('slider', { name: 'Forecast hour', exact: true })).toHaveValue('5');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('.route-option.is-selected')).toContainText('Fastest');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('.route-option.is-selected')).toContainText('Cleanest');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('.window-feature')).toBeVisible();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Progress', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.demo-director')).toContainText('6/6');
  await page.getByRole('button', { name: 'Finish', exact: true }).click();
  await expect(page.locator('.demo-director')).toHaveCount(0);
});
