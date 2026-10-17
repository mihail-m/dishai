import { test, expect, type Page } from "@playwright/test";
import { mockMapApi } from "./map-fixtures";

async function selectRenderer(page: Page, name: "Leaflet" | "MapLibre") {
  await page.getByRole("button", { name: `${name} · OSM`, exact: true }).click();
  await expect(page.locator(".map-shell")).toHaveAttribute("data-map-provider", name.toLowerCase());
  await expect(page.locator(".map-shell")).toHaveAttribute("data-map-ready", "true");
}
async function clickCenter(page: Page) {
  const box = (await page.getByRole("application", { name: "Map of Sofia street-level Clean Air Index" }).boundingBox())!;
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

test.beforeEach(async ({ page }) => {
  await mockMapApi(page);
  await page.goto("/");
  await expect(page.getByText("Background 24 µg/m³", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Streets (indicative)" }).click(); // the default map shows neighbourhood cells; these tests are about streets
});

for (const renderer of ["Leaflet", "MapLibre"] as const) {
  test(`${renderer}: street picking, hour changes and saved selection`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
    await selectRenderer(page, renderer);
    await expect(async () => {
      await clickCenter(page);
      await expect(page.getByText("Demo Avenue", { exact: true })).toBeVisible({ timeout: 1500 });
    }).toPass({ timeout: 20_000 });
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.locator("#hour")).toBeEnabled();
    await page.locator("#hour").fill("6");
    await expect(page.getByText("Background 30 µg/m³", { exact: false })).toBeVisible();
    await clickCenter(page);
    await expect(page.getByText("30 µg/m³ PM2.5", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Winter inversion demo" }).click();
    await expect(page.getByText("Demo scenario: simulated winter inversion")).toBeVisible();
    await page.reload();
    await expect(page.locator(".map-shell")).toHaveAttribute("data-map-provider", renderer.toLowerCase());
    expect(errors).toEqual([]);
  });

  test(`${renderer}: walking/cycling routes and picking both endpoints`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
    await selectRenderer(page, renderer);
    await page.getByRole("tab", { name: "Route planner" }).click();
    await expect(page.getByRole("link", { name: "Open cleanest in Google Maps" })).toBeVisible();
    // Wait for the fit-to-route animation before placing endpoints.
    await page.waitForTimeout(1100);
    for (const endpoint of ["A:", "B:"]) {
      await page.getByRole("button", { name: new RegExp(`^${endpoint}`) }).click();
      const request = page.waitForRequest((r) => new URL(r.url()).pathname === "/api/route");
      await clickCenter(page);
      const url = new URL((await request).url());
      expect(url.searchParams.get(endpoint === "A:" ? "from" : "to")).toMatch(/^42\./);
      await expect(page.getByRole("button", { name: "Click the map…" })).toHaveCount(0);
      await page.waitForTimeout(1100);
    }
    const cycle = page.waitForRequest((r) => r.url().includes("/api/route") && r.url().includes("mode=bike"));
    await page.getByRole("button", { name: "Cycle", exact: false }).click();
    await cycle;
    await expect(page.getByText("Clean-air cycling route", { exact: false })).toBeVisible();
    await page.getByRole("slider", { name: "Faster to cleaner" }).fill("1.3");
    await expect(page.getByText("setting 1.3")).toBeVisible();
    await selectRenderer(page, renderer === "Leaflet" ? "MapLibre" : "Leaflet");
    await expect(page.getByText("setting 1.3")).toBeVisible();
    await expect(page.getByText("Clean-air cycling route", { exact: false })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open cleanest in Google Maps" })).toBeVisible();
    expect(errors).toEqual([]);
  });

  test(`${renderer}: area/heat tooltips, chokepoints and animated flows`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
    await selectRenderer(page, renderer);
    await page.getByRole("tab", { name: "Clean-air network" }).click();
    await expect(page.getByText("Demo Junction", { exact: true })).toBeVisible();
    const box = (await page.getByRole("application", { name: "Map of Sofia street-level Clean Air Index" }).boundingBox())!;
    await expect(async () => {
      await page.mouse.move(0, 0);
      await page.mouse.move(box.x + box.width / 2 + 25, box.y + box.height / 2 - 25);
      await expect(page.getByText("Area of ~3000 m", { exact: false })).toBeVisible({ timeout: 1500 });
    }).toPass({ timeout: 15_000 });
    await page.getByRole("button", { name: "Cool and hot spots" }).click();
    await expect(async () => {
      await page.mouse.move(0, 0);
      await page.mouse.move(box.x + box.width / 2 + 25, box.y + box.height / 2 - 25);
      await expect(page.getByText("Surface 35 °C", { exact: false })).toBeVisible({ timeout: 1500 });
    }).toPass({ timeout: 15_000 });
    await page.getByRole("checkbox", { name: "Animate route flow" }).check();
    await page.mouse.move(0, 0);
    const before = await page.getByRole("application", { name: "Map of Sofia street-level Clean Air Index" }).screenshot();
    await expect.poll(async () => (await page.getByRole("application", { name: "Map of Sofia street-level Clean Air Index" }).screenshot()).equals(before)).toBe(false);
    await page.getByRole("button", { name: /Demo Junction/ }).click();
    await expect(page.getByText("Demo measure")).toBeVisible();
    await selectRenderer(page, renderer === "Leaflet" ? "MapLibre" : "Leaflet");
    await expect(page.getByText("Demo measure")).toBeVisible();
    await page.getByRole("tab", { name: "For me", exact: true }).click();
    await page.getByRole("tab", { name: "Clean-air network" }).click();
    await expect(page.locator(".map-shell")).toHaveAttribute("data-map-ready", "true");
    expect(errors).toEqual([]);
  });
}

test("rapid renderer switches leave a single working map", async ({ page }) => {
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "Leaflet · OSM", exact: true }).click();
    await page.getByRole("button", { name: "MapLibre · OSM", exact: true }).click();
  }
  await selectRenderer(page, "Leaflet");
  await expect(page.locator(".map-canvas")).toHaveCount(1);
  await expect(async () => {
    await clickCenter(page);
    await expect(page.getByText("Demo Avenue", { exact: true })).toBeVisible({ timeout: 1500 });
  }).toPass({ timeout: 20_000 });
});

test("panning, zooming, resizing and switching keep streets aligned", async ({ page }) => {
  await selectRenderer(page, "Leaflet");
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await page.waitForTimeout(500);
  const viewport = page.getByRole("application", { name: "Map of Sofia street-level Clean Air Index" });
  const box = (await viewport.boundingBox())!;
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 80, y + 60, { steps: 12 });
  await page.waitForTimeout(250); // release without inertial scrolling
  await page.mouse.up();
  for (const renderer of ["Leaflet", "MapLibre", "Leaflet"] as const) {
    await selectRenderer(page, renderer);
    await expect(async () => {
      await page.mouse.click(x + 80, y + 60);
      await expect(page.getByText("Demo Avenue", { exact: true })).toBeVisible({ timeout: 1500 });
    }).toPass({ timeout: 15_000 });
    await page.getByRole("button", { name: "Close", exact: true }).click();
  }
  await page.setViewportSize({ width: 980, height: 900 });
  await page.getByRole("button", { name: "Reset view" }).click();
  await page.waitForTimeout(1100);
  await expect(async () => {
    await clickCenter(page);
    await expect(page.getByText("Demo Avenue", { exact: true })).toBeVisible({ timeout: 1500 });
  }).toPass({ timeout: 15_000 });
});
