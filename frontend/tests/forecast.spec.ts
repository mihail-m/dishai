import { test, expect } from "@playwright/test";
import { forecastWindow, mockMapApi } from "./map-fixtures";

test.beforeEach(async ({ page }) => {
  await mockMapApi(page);
  await page.goto("/");
  await expect(page.locator("#hour")).toBeEnabled();
  await page.getByRole("button", { name: "Streets (indicative)" }).click(); // the default map shows neighbourhood cells; these tests are about streets
});

test("the default map shows neighbourhood cells, streets are an indicative option", async ({ page }) => {
  await page.getByRole("button", { name: "Neighbourhoods" }).click();
  await expect(page.getByRole("button", { name: "Neighbourhoods" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("neighbourhoods (~250 m)")).toBeVisible();
  await page.getByRole("button", { name: "Streets (indicative)" }).click();
  await expect(page.getByText(/Street colours are indicative/)).toBeVisible();
});

test("all 24 hours update from memory without requests", async ({ page }) => {
  await expect(page.locator("#hour")).toHaveAttribute("max", "23");
  await expect(page.locator("#hour")).toHaveValue("0");
  const requests: string[] = [];
  await page.route("**/api/**", (route) => { requests.push(route.request().url()); return route.abort(); });
  for (let i = 0; i < 24; i++) {
    await page.locator("#hour").fill(String(i));
    await expect(page.getByText(`Background ${24 + i} µg/m³`, { exact: false })).toBeVisible();
  }
  expect(requests).toEqual([]);
});

test("refresh replaces the whole window and preserves the selected time", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/forecast/refresh?*", async (route) => {
    expect(route.request().method()).toBe("POST");
    await gate;
    await route.fulfill({ json: forecastWindow(null, 1, 13) });
  });
  await page.getByRole("button", { name: "Refresh forecast" }).click();
  await expect(page.getByRole("button", { name: "Refreshing…" })).toBeDisabled();
  await page.locator("#hour").fill("6");
  await expect(page.getByText("Background 30 µg/m³", { exact: false })).toBeVisible();
  release();
  await expect(page.getByRole("button", { name: "Refresh forecast" })).toBeEnabled();
  await expect(page.locator("#hour")).toHaveValue("5");
  await expect(page.getByText("Background 40 µg/m³", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Now", exact: true }).click();
  await expect(page.locator("#hour")).toHaveValue("0");
  await expect(page.getByText("Background 35 µg/m³", { exact: false })).toBeVisible();
});

test("a failed refresh keeps the previous forecast and can be retried", async ({ page }) => {
  await page.route("**/api/forecast/refresh?*", (route) => route.fulfill({ status: 503, json: { detail: "Upstream unavailable" } }));
  await page.getByRole("button", { name: "Refresh forecast" }).click();
  await expect(page.getByRole("alert")).toContainText("last loaded forecast is kept");
  await expect(page.locator("#hour")).toBeEnabled();
  await page.locator("#hour").fill("23");
  await expect(page.getByText("Background 47 µg/m³", { exact: false })).toBeVisible();
  await page.unroute("**/api/forecast/refresh?*");
  await page.getByRole("button", { name: "Refresh forecast" }).click();
  await expect(page.getByText("Background 57 µg/m³", { exact: false })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const tab of ["Route planner", "Clean-air network"] as const) {
  test(`${tab} preloads 24 hours and scrubs without network requests`, async ({ page }) => {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await expect(page.locator("#hour")).toBeEnabled();
    await expect(page.getByText("24 hours ready · instant playback")).toBeVisible();
    const requests: string[] = [];
    await page.route("**/api/**", (route) => { requests.push(route.request().url()); return route.abort(); });
    for (const i of [23, 4, 15, 0]) {
      await page.locator("#hour").fill(String(i));
      await expect(page.locator("#hour")).toHaveValue(String(i));
    }
    await page.waitForTimeout(400); // catches the old debounced fetch behaviour
    expect(requests).toEqual([]);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

test("a late response from a previous scenario cannot replace the new one", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/forecast/refresh?*", async (route) => {
    if (!new URL(route.request().url()).searchParams.has("scenario")) await gate;
    await route.fulfill({ json: forecastWindow(new URL(route.request().url()).searchParams.get("scenario"), 1) }).catch(() => {});
  });
  await page.getByRole("button", { name: "Refresh forecast" }).click();
  await page.getByRole("button", { name: "Winter inversion demo" }).click();
  await expect(page.getByText("Demo scenario: simulated winter inversion")).toBeVisible();
  release();
  await page.waitForTimeout(200);
  await expect(page.getByText("Demo scenario: simulated winter inversion")).toBeVisible();
  await expect(page.locator("#hour")).toHaveValue("0");
});

test("selected street details also change instantly with the hour", async ({ page }) => {
  await page.getByRole("button", { name: "Leaflet · OSM", exact: true }).click();
  const box = (await page.locator(".map-viewport").boundingBox())!;
  await expect(async () => {
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(page.getByText("Demo Avenue", { exact: true })).toBeVisible({ timeout: 1500 });
  }).toPass({ timeout: 20_000 });
  await expect(page.locator("#hour")).toBeEnabled();
  const requests: string[] = [];
  await page.route("**/api/**", (route) => { requests.push(route.request().url()); return route.abort(); });
  await page.locator("#hour").fill("6");
  await expect(page.getByText("30 µg/m³ PM2.5", { exact: true })).toBeVisible();
  await page.locator("#hour").fill("23");
  await expect(page.getByText("47 µg/m³ PM2.5", { exact: true })).toBeVisible();
  expect(requests).toEqual([]);
});

test("partial preload failure stays visible and refresh retries it", async ({ page }) => {
  await page.route("**/api/route?*", async (route) => {
    const hour = new URL(route.request().url()).searchParams.get("hour");
    if (hour === "13") await route.fulfill({ status: 503, json: { detail: "Route hour unavailable" } });
    else { await new Promise((resolve) => setTimeout(resolve, 100)); await route.fallback(); }
  });
  await page.getByRole("tab", { name: "Route planner" }).click();
  await expect(page.getByRole("alert")).toContainText("Route hour unavailable");
  await page.waitForTimeout(300);
  await expect(page.getByRole("alert")).toContainText("Route hour unavailable");
  await expect(page.locator("#hour")).toBeDisabled();
  await page.unroute("**/api/route?*");
  await page.getByRole("button", { name: "Refresh forecast" }).click();
  await expect(page.locator("#hour")).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
});
