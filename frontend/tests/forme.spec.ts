import { test, expect } from "@playwright/test";
import { mockMapApi } from "./map-fixtures";

const heat = {
  id: "heat-2026-10-17", kind: "heat", level: "warning", title: "Extreme heat: feels like 39 °C", message: "Peak 39 °C around 15:00, today 11:00–20:00.", start: "2026-10-17T11:00", end: "2026-10-17T20:00",
  in_progress: true, peak: null, cause: [], source: "Open-Meteo forecast", confidence: "forecast", audience: "everyone",
  actions: [{ id: "shade", title: "Stay in the coolest place you can", body: "Avoid exertion at midday." }],
};
const districts = { type: "FeatureCollection", meta: {}, features: [
  { type: "Feature", properties: { name: "Sredets", centroid: [23.33, 42.69] }, geometry: { type: "Polygon", coordinates: [[[23.30, 42.67], [23.36, 42.67], [23.36, 42.72], [23.30, 42.72], [23.30, 42.67]]] } },
  { type: "Feature", properties: { name: "Lozenets", centroid: [23.32, 42.66] }, geometry: { type: "Polygon", coordinates: [[[23.30, 42.60], [23.36, 42.60], [23.36, 42.665], [23.30, 42.665], [23.30, 42.60]]] } },
] };
const advice = { cards: [
  { id: "heat", title: "Heat 10:00 to 19:00: plan around it", body: "duplicate of the alert", severity: "action", measure: null },
  { id: "stay_in", title: "Limit time outdoors", body: "Keep exertion low.", severity: "action", measure: { id: "A.3.4", label: "A.3.4 early warning" } },
  { id: "renovation", title: "Replace the stove", body: "Support exists.", severity: "action", cta: "eligibility_wizard", measure: { id: "C.3.2", label: "C.3.2 home renovation" } },
], zone: { zone: null }, grant_note: "Up to about 10,000 EUR.", district: "Sredets" };

test.beforeEach(async ({ page }) => {
  await mockMapApi(page);
  await page.route("**/api/districts*", (route) => route.fulfill({ json: districts }));
  await page.route("**/api/advice*", (route) => route.fulfill({ json: advice }));
  await page.route("**/api/alerts*", (route) => route.fulfill({ json: { generated_at: "2026-10-17T12:00", scenario: null, alerts: [heat], sources: {}, push: { enabled: false } } }));
});

test("one verdict first, then what to do, the day and the setup", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "For me", exact: true }).first().click();
  const verdict = page.getByRole("status", { name: "Right now" });
  await expect(verdict.getByRole("heading", { name: "Dangerous heat today" })).toBeVisible();
  const plan = page.getByRole("region", { name: "What to do" });
  await expect(plan.getByRole("heading", { name: "Stay in the coolest place you can" })).toBeVisible();
  await expect(plan.getByRole("heading", { name: "Limit time outdoors" })).toBeVisible();
  await expect(plan.getByText("Heat 10:00 to 19:00")).toHaveCount(0); // the alert already covers heat
  await expect(page.getByRole("region", { name: "The next 24 hours" })).toBeVisible();
  // programmes sit behind one line, with plan jargon only as a footnote
  await expect(page.getByText("Replace the stove")).toBeHidden();
  await page.getByText("City programmes you may qualify for").click();
  await expect(page.getByText("Replace the stove")).toBeVisible();
  await expect(page.getByText("Sofia climate plan: C.3.2 home renovation")).toBeVisible();
});

test("choices are saved on this device and old saved choices still load", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("dishai.foryou", JSON.stringify({ profile: "vulnerable", toggles: ["asthma", "pollen_allergy"], species: ["birch"] })));
  await page.goto("/");
  await page.getByRole("button", { name: "For me", exact: true }).first().click();
  await expect(page.getByRole("button", { name: "I am older or health-sensitive" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Asthma" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Birch" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Alder" }).click();
  await page.getByRole("button", { name: "Pollen allergy" }).click(); // off: the trigger list goes away
  await expect(page.getByRole("button", { name: "Alder" })).toHaveCount(0);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("dishai.foryou")!));
  expect(saved).toMatchObject({ profile: "vulnerable", toggles: ["asthma"], species: ["birch", "alder"] });
});

test("a position fix selects the district it falls in", async ({ page, context }) => {
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 42.63, longitude: 23.32 }); // inside the second polygon
  await page.goto("/");
  await page.getByRole("button", { name: "For me", exact: true }).first().click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Sredets");
  await page.getByRole("button", { name: "Use my location" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Lozenets");
  await expect(page.getByRole("button", { name: /Using your location/ })).toBeVisible();
});
