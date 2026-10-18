import { test, expect } from '@playwright/test';

let pageErrors: string[] = [];
test.beforeEach(async ({ page }) => { pageErrors = []; page.on('pageerror', error => pageErrors.push(error.message)); });
test.afterEach(() => { expect(pageErrors, 'No uncaught errors during navigation').toEqual([]); });

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
  await page.getByRole('button', { name: /Lower exposure.*min/ }).click();
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
  const hours = Array.from({ length: 24 }, (_, i) => ({ time: new Date(Date.UTC(2026, 9, 18, 9 + i)).toISOString().slice(0, 16), pm25_model: 12 + i, t2m: 18, wind10m: 4, cloud: 10, is_night: false }));
  const payload = (revision: number) => ({ id: `snapshot-${revision}`, hours: hours.map((_, i) => i + 30), values: hours.map((hour, i) => ({ time: hour.time, hour: i + 30 })), forecast: { hours: hours.map(h => ({ ...h, pm25_model: h.pm25_model + revision })), generated_at: hours[0].time, source: 'fixture', note: 'Saved data.' }, sensors: { sensors: [] } });
  await page.route('**/api/forecast/*', async route => { forecastCalls++; await route.fulfill({ json: payload(forecastCalls) }); });
  await page.route('**/api/route?*', async route => {
    routeCalls++;
    const hour = Number(new URL(route.request().url()).searchParams.get('hour'));
    const option = { time_min: 16, distance_m: 1200, mean_pm25: 11, dose_change_pct: -8, polyline: [[23.333122, 42.6915547], [23.329, 42.693], [23.3249083, 42.6955847]] };
    await route.fulfill({ json: { hour, time: hours[hour - 30].time, routes: { fastest: { ...option, dose_change_pct: 0 }, cleanest: option }, confidence: { cleaner_exists: false, message: 'Within uncertainty.' }, note: 'Test route' } });
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
  await expect(page.getByRole('button', { name: 'Preview this route' })).toBeEnabled();
  expect(routeCalls).toBe(24);
  await expect(page.getByRole('button', { name: /Alternative route.*min/ })).toBeVisible();
  await expect(page.locator('.exposure-insight')).toContainText('within model uncertainty');
  await page.getByRole('slider', { name: 'Route departure hour' }).fill('20');
  expect(routeCalls).toBe(24);
});

test('small screens and reduced motion retain reachable navigation and dialogs', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Understand air quality' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  for (const name of ['Routes', 'My day', 'Today']) {
    await page.getByRole('button', { name, exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const nav = await page.locator('.glass-tabbar').boundingBox();
    expect(nav!.y + nav!.height).toBeLessThanOrEqual(740);
  }
});

test('guided presentation takes the user through all five demo moments', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'App settings' }).click();
  await page.getByRole('button', { name: 'Take the demo tour' }).click();
  await expect(page.locator('.demo-director')).toContainText('1/5');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.getByRole('slider', { name: 'Forecast hour', exact: true })).toHaveValue('5');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('.route-option.is-selected')).toContainText('Fastest');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('.route-option.is-selected')).toContainText('Lower exposure');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page.locator('.window-feature')).toBeVisible();
  await page.getByRole('button', { name: 'Finish', exact: true }).click();
  await expect(page.locator('.demo-director')).toHaveCount(0);
});
