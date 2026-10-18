import { test, expect } from "@playwright/test";
import { mockMapApi } from "./map-fixtures";

test.beforeEach(async ({ page }) => {
  await mockMapApi(page);
  await page.goto("/");
  await page.getByRole("navigation", { name: "Views" }).first().getByRole("button", { name: "My health" }).click();
});

test("the My health tab shows the pollen forecast by plant, the season calendar and other triggers", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Pollen and allergy" })).toBeVisible();
  await expect(page.getByText(/High birch pollen and 1 more now/)).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Birch/ })).toBeVisible();
  await expect(page.getByRole("img", { name: /Birch pollen, hourly for the next four days/ })).toBeVisible();
  await expect(page.getByText("Usually starts in about 16 days, around 1 May.")).toBeVisible();
  await expect(page.getByRole("group", { name: /Typical pollen season/ }).getByRole("button")).toHaveCount(6 * 36);
  await expect(page.getByText("UV index")).toBeVisible();
  await expect(page.getByText("Mineral dust")).toBeVisible();
});

test("choosing the pollen I react to puts those plants first and is remembered", async ({ page }) => {
  await page.getByRole("button", { name: "Pollen allergy" }).click();
  await page.getByRole("group", { name: "Pollen types" }).getByRole("button", { name: "Grass" }).click();
  await expect(page.getByText("Your trigger")).toHaveCount(1);
  const first = page.locator("article").filter({ hasText: "Your trigger" });
  await expect(first.getByRole("heading", { name: /^Grass/ })).toBeVisible();
  await page.reload();
  await page.getByRole("navigation", { name: "Views" }).first().getByRole("button", { name: "My health" }).click();
  await expect(page.getByRole("group", { name: "Pollen types" }).getByRole("button", { name: "Grass" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Pollen allergy" })).toHaveAttribute("aria-pressed", "true");
});

test("calendar squares explain themselves and the symptom check-in asks once a day", async ({ page }) => {
  await page.getByRole("group", { name: /Typical pollen season/ }).getByRole("button", { name: /^Birch, Apr 1–10/ }).hover();
  await expect(page.getByText(/Birch · Apr 1–10: typically up to 120 grains/)).toBeVisible();
  await page.getByRole("button", { name: "Mild" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Thanks" })).toBeVisible();
  await page.reload();
  await page.getByRole("navigation", { name: "Views" }).first().getByRole("button", { name: "My health" }).click();
  await expect(page.getByRole("button", { name: "Mild" })).toHaveCount(0);
});

test("the route tab has no settings: from, to, and the alternatives with time and pollution saved", async ({ page }) => {
  await page.getByRole("navigation", { name: "Views" }).first().getByRole("button", { name: "Route" }).click();
  const options = page.getByRole("region", { name: "Route options" });
  await expect(options.getByText("17% less polluted air")).toBeVisible();
  await expect(options.getByText("+3 min")).toBeVisible();
  await expect(options.getByRole("row", { name: /Fastest/ })).toBeVisible();
  await expect(options.getByRole("row", { name: /Cleanest/ })).toContainText("-17%");
  await expect(options.getByRole("link", { name: /Open cleanest in Google Maps/ })).toBeVisible();
  await expect(options.getByRole("link", { name: /Open fastest/ })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Route planner" }).getByRole("slider")).toHaveCount(0);
  await expect(page.getByRole("complementary", { name: "Route planner" }).getByRole("checkbox")).toHaveCount(0);
});
