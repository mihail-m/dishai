import { test, expect } from "@playwright/test";
import { mockMapApi } from "./map-fixtures";

const heat = {
  id: "heat-2026-10-17", kind: "heat", level: "warning", title: "Extreme heat: feels like 39 °C", message: "Peak 39 °C around 15:00, today 11:00–20:00.", start: "2026-10-17T11:00", end: "2026-10-17T20:00",
  in_progress: true, peak: { value: 39, unit: "°C", time: "2026-10-17T15:00" }, cause: [], source: "Open-Meteo forecast", confidence: "forecast", audience: "everyone",
  actions: [{ id: "shade", title: "Stay in the coolest place you can", body: "Avoid exertion at midday." }, { id: "water", title: "Drink water before you are thirsty", body: "" }],
};
const pm = {
  id: "pm-2026-10-18", kind: "pm", level: "watch", title: "Possible pollution episode", message: "PM2.5 around 30 µg/m³ tomorrow 08:00–14:00 (40% chance).", start: "2026-10-18T08:00", end: "2026-10-18T14:00",
  in_progress: false, peak: null, cause: ["dust"], source: "DishAI PM2.5 model", confidence: "model", audience: "everyone", actions: [{ id: "airing", title: "Air your home before it starts", body: "" }],
};

test.beforeEach(async ({ page }) => {
  await mockMapApi(page);
  await page.route("**/api/alerts*", (route) => route.fulfill({ json: { generated_at: "2026-10-17T12:00", scenario: null, alerts: [heat, pm], sources: {}, push: { enabled: false } } }));
});

test("alerts lead the panel and their first action leads the suggestions", async ({ page }) => {
  await page.goto("/");
  const strip = page.getByRole("region", { name: "Alerts" });
  await expect(strip.getByRole("heading", { name: "Extreme heat: feels like 39 °C" })).toBeVisible();
  await expect(strip.getByRole("heading", { name: "Possible pollution episode" })).toBeVisible();
  await expect(strip.getByText("Likely contributing: desert dust")).toBeVisible();
  await strip.getByText("What to do").first().click();
  await expect(strip.getByText("Drink water before you are thirsty.")).toBeVisible();
  // the alert under way becomes the first suggestion; an alert starting tomorrow morning (20 h away) does not
  await page.getByRole("button", { name: "Neighbourhoods" }).click();
  const sugg = page.getByRole("region", { name: "Suggestions for you" });
  await expect(sugg.getByRole("heading", { level: 4 }).first()).toHaveText("Stay in the coolest place you can");
  await expect(sugg.getByRole("heading", { name: "Air your home before it starts" })).toHaveCount(0);
});

test("turning on notifications works without push keys and says what that means", async ({ page, context }) => {
  await context.grantPermissions(["notifications"]);
  await page.goto("/");
  await page.getByText("Notifications", { exact: true }).click();
  await page.getByRole("button", { name: "Smoke" }).click(); // untick a type
  await page.getByRole("combobox", { name: "Minimum alert level" }).selectOption("warning");
  await page.getByRole("button", { name: "Turn on notifications" }).click();
  await expect(page.getByRole("status").filter({ hasText: "On while DishAI is open" })).toBeVisible();
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("dishai.alerts")!));
  expect(saved).toMatchObject({ enabled: true, level: "warning", mode: "page", kinds: ["pm", "heat", "dust", "transport"] });
  await page.getByRole("button", { name: "Turn off" }).click();
  await expect(page.getByRole("button", { name: "Turn on notifications" })).toBeVisible();
});

test("no alerts means no banner, only the quiet notification settings", async ({ page }) => {
  await page.route("**/api/alerts*", (route) => route.fulfill({ json: { generated_at: "2026-10-17T12:00", scenario: null, alerts: [], sources: {}, push: { enabled: false } } }));
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Alerts" }).getByRole("article")).toHaveCount(0);
  await expect(page.getByText("Notifications", { exact: true })).toBeVisible();
});

test("alerts are requested once, not on every render", async ({ page }) => {
  let n = 0;
  await page.route("**/api/alerts*", (route) => { n++; return route.fulfill({ json: { generated_at: "2026-10-17T12:00", scenario: null, alerts: [], sources: {}, push: { enabled: false } } }); });
  await page.goto("/");
  await expect(page.locator("#hour")).toBeEnabled();
  for (let i = 0; i < 6; i++) await page.locator("#hour").fill(String(i));
  await page.getByRole("button", { name: "Heat", exact: true }).click();
  await page.getByRole("button", { name: "Air quality", exact: true }).click();
  expect(n).toBe(1);
});

const ticket = {
  id: "transport-2026-10-18", kind: "transport", level: "watch", title: "Green ticket tomorrow: ride public transport", message: "Sofia Municipality has announced the green ticket for tomorrow.",
  start: "2026-10-18T00:00", end: "2026-10-19T00:00", in_progress: false, peak: null, cause: [], source: "Sofia Municipality announcement: https://example.org/a", confidence: "forecast", audience: "everyone",
  detail: { green_ticket: true, source_url: "https://example.org/a" }, actions: [{ id: "transit", title: "Take the metro, tram or bus", body: "Fewer cars on the street." }],
};
const plumes = {
  generated_at: "2026-10-17T12:00", scenario: null, fire_threat: null, sources: { cams_dust: "live", fires: { enabled: true, detections: 221 } },
  green_ticket: [{ date: "2026-10-18", source: "https://example.org/a", note: "" }],
  plume: { yardstick: true, hours: [], plume: { kind: "dust", components: ["dust"], label: "Desert dust: coarse particles", evidence: ["CAMS mineral dust up to 45 µg/m³ at the surface, 60% of PM10"], vertical: "The forecast brings the dust to the surface (up to 45 µg/m³ in the lowest model level)." },
    days: [{ date: "2026-10-17", pm10: 52, pm25: 21, usual_pm10: 20, usual_pm25: 14, worse_than_usual: true, worse_by: "pm10", dust_mean: 30, dust_max: 45, dust_share: 0.6 },
           { date: "2026-10-18", pm10: 18, pm25: 12, usual_pm10: 20, usual_pm25: 14, worse_than_usual: false, worse_by: null, dust_mean: 1, dust_max: 3, dust_share: 0.05 }] },
};

test("a green ticket is a watch alert with a link, and the regional card shows CAMS next to the month's usual", async ({ page }) => {
  await page.route("**/api/alerts*", (route) => route.fulfill({ json: { generated_at: "2026-10-17T12:00", scenario: null, alerts: [ticket], sources: {}, push: { enabled: false } } }));
  await page.route("**/api/plumes*", (route) => route.fulfill({ json: plumes }));
  await page.goto("/");
  const strip = page.getByRole("region", { name: "Alerts" });
  await expect(strip.getByRole("heading", { name: "Green ticket tomorrow: ride public transport" })).toBeVisible();
  await strip.getByText("What to do").first().click();
  await expect(strip.getByRole("link", { name: "Announcement" })).toHaveAttribute("href", "https://example.org/a");
  await page.getByRole("button", { name: "For me", exact: true }).first().click();
  await page.getByText("Dust, smoke and regional air").click();
  await expect(page.getByText("Desert dust: coarse particles.")).toBeVisible();
  await expect(page.getByText("worse than usual")).toBeVisible();
  await expect(page.getByText("Green ticket: announced for Tomorrow.")).toBeVisible();
});

test("without an announcement the card says so and does not invent one", async ({ page }) => {
  await page.route("**/api/plumes*", (route) => route.fulfill({ json: { ...plumes, green_ticket: [] } }));
  await page.goto("/");
  await page.getByRole("button", { name: "For me", exact: true }).first().click();
  await page.getByText("Dust, smoke and regional air").click();
  await expect(page.getByText("Green ticket: none announced.")).toBeVisible();
});
