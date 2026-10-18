import { test, expect } from "@playwright/test";
import { forecastWindow, mockMapApi, openMenu, pickProvider } from "./map-fixtures";

test.beforeEach(async ({ page }) => {
  await mockMapApi(page);
  await page.goto("/");
  await expect(page.locator("#hour")).toBeEnabled();
  await page.getByRole("button", { name: "Streets", exact: true }).click(); // the default map shows neighbourhood cells; these tests are about streets
});

test("the default map shows neighbourhood cells, streets are an indicative option", async ({ page }) => {
  await page.getByRole("button", { name: "Neighbourhoods" }).click();
  await expect(page.getByRole("button", { name: "Neighbourhoods" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/Hover or tap the map to see the air quality score/)).toBeVisible();
  await page.getByRole("button", { name: "Streets", exact: true }).click();
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
  await openMenu(page);
  await page.getByRole("menuitem", { name: "Refresh forecast" }).click();
  await expect(page.getByRole("menuitem", { name: "Refreshing…" })).toBeDisabled();
  await page.locator("#hour").fill("6");
  await expect(page.getByText("Background 30 µg/m³", { exact: false })).toBeVisible();
  release();
  await expect(page.getByRole("menuitem", { name: "Refresh forecast" })).toBeEnabled();
  await expect(page.locator("#hour")).toHaveValue("5");
  await expect(page.getByText("Background 40 µg/m³", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Back to now" }).click();
  await expect(page.locator("#hour")).toHaveValue("0");
  await expect(page.getByText("Background 35 µg/m³", { exact: false })).toBeVisible();
});

test("a failed refresh keeps the previous forecast and can be retried", async ({ page }) => {
  await page.route("**/api/forecast/refresh?*", (route) => route.fulfill({ status: 503, json: { detail: "Upstream unavailable" } }));
  await openMenu(page);
  await page.getByRole("menuitem", { name: "Refresh forecast" }).click();
  await expect(page.getByRole("alert")).toContainText("last loaded forecast is kept");
  await expect(page.locator("#hour")).toBeEnabled();
  await page.locator("#hour").fill("23");
  await expect(page.getByText("Background 47 µg/m³", { exact: false })).toBeVisible();
  await page.unroute("**/api/forecast/refresh?*");
  await page.getByRole("menuitem", { name: "Refresh forecast" }).click();
  await expect(page.getByText("Background 57 µg/m³", { exact: false })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const tab of ["Route"] as const) {
  test(`${tab} preloads 24 hours and scrubs without network requests`, async ({ page }) => {
    await page.getByRole("button", { name: tab, exact: true }).click();
    await expect(page.locator("#hour")).toBeEnabled();
    await expect(page.getByText(/Preparing hours/)).toHaveCount(0);
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
  await openMenu(page);
  await page.getByRole("menuitem", { name: "Refresh forecast" }).click();
  await page.getByRole("menuitemradio", { name: /Winter inversion demo/ }).click();
  await expect(page.getByText("Demo: simulated winter inversion")).toBeVisible();
  release();
  await page.waitForTimeout(200);
  await expect(page.getByText("Demo: simulated winter inversion")).toBeVisible();
  await expect(page.locator("#hour")).toHaveValue("0");
});

test("selected street details also change instantly with the hour", async ({ page }) => {
  await pickProvider(page, "Leaflet");
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
  await page.getByRole("button", { name: "Route", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Route hour unavailable");
  await page.waitForTimeout(300);
  await expect(page.getByRole("alert")).toContainText("Route hour unavailable");
  await expect(page.locator("#hour")).toBeDisabled();
  await page.unroute("**/api/route?*");
  await openMenu(page);
  await page.getByRole("menuitem", { name: "Refresh forecast" }).click();
  await expect(page.locator("#hour")).toBeEnabled();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("the theme toggle switches between light and dark and remembers the choice", async ({ page }) => {
  const toggle = page.getByRole("button", { name: "Dark theme" });
  await expect(toggle).toHaveAttribute("aria-pressed", "false"); // the suite runs with colorScheme: light
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(11, 18, 14)");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("button", { name: "Dark theme" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(238, 243, 234)");
});

test("the forecast bar switches between PM2.5 and the air trapping score", async ({ page }) => {
  const pm = page.getByRole("button", { name: "PM2.5", exact: true }), trap = page.getByRole("button", { name: "Air trapping" });
  await expect(pm).toHaveAttribute("aria-pressed", "true");
  await trap.click();
  await expect(trap).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/Air trapping score, 0 to 100/)).toBeVisible();
  await expect(page.locator("#hour")).toBeEnabled();
  await pm.click();
  await expect(page.getByText(/Solid line: learned estimate/)).toBeVisible();
});

test("suggestions follow the current location and the forecast bar follows the layer", async ({ page, context }) => {
  await page.getByRole("button", { name: "Neighbourhoods" }).click(); // beforeEach switches to streets
  await expect(page.getByText("Advice for where you are")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Air your home briefly|Open your windows|Keep your windows closed/ })).toBeVisible(); // city-wide advice before any location
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 42.69, longitude: 23.32 });
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByRole("button", { name: "Stop sharing" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Centre on my location" })).toBeVisible();
  // the graph is the air forecast on Air quality, the temperature forecast on Heat, and a slim control on Clean paths
  await expect(page.getByRole("button", { name: "Air trapping" })).toBeVisible();
  await page.getByRole("button", { name: "Heat", exact: true }).click();
  await expect(page.getByRole("button", { name: "Feels like" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Air trapping" })).toHaveCount(0);
  await page.getByRole("button", { name: "Clean paths", exact: true }).click();
  await expect(page.getByRole("button", { name: "Feels like" })).toHaveCount(0);
  await expect(page.locator("#hour")).toHaveCount(0); // clean paths describe the city now: no time control, no options
  await expect(page.getByRole("button", { name: /^(Areas|Busy streets|Other streets|Animate|Relative|Absolute)/ })).toHaveCount(0);
});
