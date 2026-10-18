import { test, expect, type Page } from '@playwright/test';

let pageErrors: string[] = [];
test.beforeEach(async ({ page }) => { pageErrors = []; page.on('pageerror', error => pageErrors.push(error.message)); });
test.afterEach(() => { expect(pageErrors, 'Progress and the existing app must remain free of uncaught errors').toEqual([]); });

async function openProgress(page: Page) {
  await page.getByRole('button', { name: 'Progress', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Progress', exact: true })).toHaveAttribute('aria-current', 'page');
}

async function expectNavigationVisible(page: Page) {
  const navigation = page.getByRole('navigation', { name: 'Mobile navigation' });
  await expect(navigation.getByRole('button')).toHaveCount(4);
  for (const label of ['Today', 'Routes', 'My day', 'Progress']) {
    await expect(navigation.getByRole('button', { name: label, exact: true })).toBeInViewport({ ratio: 1 });
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test('sample weekly progress explains its estimates and lets you inspect individual days', async ({ page }) => {
  const apiCalls: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/')) apiCalls.push(request.url()); });
  await page.goto('/');
  await openProgress(page);
  await expect(page.locator('.progress-page')).toContainText('DEMO ACTIVITY · SAMPLE WEEK');
  await expect(page.locator('.progress-page')).toContainText('12–18 October 2026');
  await expect(page.getByRole('heading', { name: 'Your choices are adding up.' })).toBeVisible();
  await expect(page.locator('.progress-streak')).toHaveText('5');
  await expect(page.locator('.progress-points')).toHaveText('300');
  await expect(page.locator('.progress-challenge-count')).toHaveText('5');
  await expect(page.locator('.progress-metric--air')).toContainText('−28.6%');
  await expect(page.locator('.progress-metric--carbon')).toContainText('4.5kg saved');
  await expect(page.locator('.progress-day')).toHaveCount(7);
  await page.getByRole('button', { name: /^Monday 12 October,/ }).click();
  await expect(page.getByRole('button', { name: /^Monday 12 October,/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.progress-day-detail')).toContainText('Monday');
  await page.getByRole('button', { name: /^Sunday 18 October,/ }).click();
  await expect(page.getByRole('button', { name: /^Sunday 18 October,/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.progress-day-detail')).toContainText('Sunday');
  await page.getByRole('button', { name: 'How estimates are calculated', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'About your progress' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('PM2.5');
  await expect(dialog).toContainText('CO₂');
  await expect(dialog.getByRole('button', { name: 'Reset demo progress', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(apiCalls).toEqual([]);
});

test('adding one demo walk completes the weekly challenge once, persists, and can be reset', async ({ page }) => {
  await page.goto('/');
  await openProgress(page);
  const milestone = page.locator('.progress-milestone[aria-label="Weekly six milestone"]');
  const transportEstimate = await page.locator('.progress-metric--carbon').textContent();
  await expect(milestone).toHaveAttribute('data-earned', 'false');
  await page.getByRole('button', { name: /^Add demo walk/ }).click();
  await expect(page.locator('.progress-points')).toHaveText('360');
  await expect(page.locator('.progress-streak')).toHaveText('6');
  await expect(page.locator('.progress-challenge-count')).toHaveText('6');
  await expect(page.locator('.progress-metric--air')).toContainText('−29.2%');
  await expect(milestone).toHaveAttribute('data-earned', 'true');
  await expect(milestone).toContainText('Unlocked');
  // A recreational walk must not silently invent an avoided car journey.
  await expect(page.locator('.progress-metric--carbon')).toHaveText(transportEstimate!);
  await expect(page.getByRole('button', { name: /^Demo walk added/ })).toBeDisabled();
  await page.reload();
  await openProgress(page);
  await expect(page.locator('.progress-points')).toHaveText('360');
  await expect(page.locator('.progress-streak')).toHaveText('6');
  await expect(page.locator('.progress-challenge-count')).toHaveText('6');
  await expect(milestone).toHaveAttribute('data-earned', 'true');
  await expect(page.getByRole('button', { name: /^Demo walk added/ })).toBeDisabled();
  await page.getByRole('button', { name: 'How estimates are calculated', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reset demo progress', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.progress-points')).toHaveText('300');
  await expect(page.locator('.progress-streak')).toHaveText('5');
  await expect(page.locator('.progress-challenge-count')).toHaveText('5');
  await expect(milestone).toHaveAttribute('data-earned', 'false');
  await expect(page.getByRole('button', { name: /^Add demo walk/ })).toBeEnabled();
  await page.reload();
  await openProgress(page);
  await expect(page.locator('.progress-points')).toHaveText('300');
});

test('progress stays usable at 320px and its route action preserves the existing forecast selection', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('slider', { name: 'Forecast hour', exact: true }).fill('4');
  await openProgress(page);
  await expectNavigationVisible(page);
  await page.getByRole('button', { name: /^Plan a cleaner route/ }).click();
  await expect(page.getByRole('button', { name: 'Routes', exact: true })).toHaveAttribute('aria-current', 'page');
  await expect(page.getByRole('slider', { name: 'Route departure hour' })).toBeEnabled();
  await expect(page.getByRole('slider', { name: 'Route departure hour' })).toHaveValue('4');
  await expect(page.locator('.route-map-route')).toHaveCount(3);
  for (const label of ['My day', 'Today', 'Progress']) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await expectNavigationVisible(page);
  }
  await page.getByRole('button', { name: 'How estimates are calculated', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'About your progress' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Today', exact: true }).click();
  await expect(page.getByRole('slider', { name: 'Forecast hour', exact: true })).toHaveValue('4');
  await expect(page.locator('.particle-reading strong')).toHaveText('7.8');
});

test('sample progress remains available when the connected forecast service fails', async ({ page }) => {
  await page.route('**/api/forecast/window', route => route.fulfill({ status: 503, json: { detail: 'Forecast service is offline' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'App settings' }).click();
  await page.getByRole('button', { name: /Connect to AirPath/ }).click();
  await expect(page.getByRole('alert')).toContainText('Forecast service is offline');
  await openProgress(page);
  await expect(page.locator('.data-chip')).toHaveText('Connected');
  await expect(page.locator('.progress-page')).toContainText('DEMO ACTIVITY · SAMPLE WEEK');
  await expect(page.getByRole('heading', { name: 'Your choices are adding up.' })).toBeVisible();
  await expect(page.locator('.progress-points')).toHaveText('300');
  await page.getByRole('button', { name: /^Add demo walk/ }).click();
  await expect(page.locator('.progress-points')).toHaveText('360');
  await expect(page.getByRole('button', { name: /^Demo walk added/ })).toBeDisabled();
  await expect(page.locator('.loading-state')).toHaveCount(0);
});
