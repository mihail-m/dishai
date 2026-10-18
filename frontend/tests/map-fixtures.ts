import type { Page } from "@playwright/test";

const line = [[23.29, 42.69], [23.35, 42.69]];
const time = "2026-10-17T12:00";
const alert = { active: false, peak_score: 20, peak_time: time, message: "Air quality demo" };
const hours = Array.from({ length: 48 }, (_, i) => ({
  time: `2026-10-${17 + Math.floor(i / 24)}T${String(i % 24).padStart(2, "0")}:00`,
  score: 20 + i, label: "good", blh: 800, wind10m: 10, t2m: 18, t925: 14, inversion_delta: -4,
  wind925: 12, cloud: 20, pm25: 12 + i, pm10: 20, aqi: 25, is_night: false,
}));
const choke = { rank: 1, street: "Demo Junction", street_bg: "Демо", district: "Sredets", road_class: "primary", score: 10,
  max_fast_usage: 20, mean_cai: 40, length_m: 100, lon: 23.34, lat: 42.705,
  segments: [[[23.335, 42.705], [23.345, 42.705]]], measures: [{ id: "A.4.7", label: "Street washing", why: "Demo measure" }] };

export function forecastWindow(scenario: string | null = null, revision = 0, start = 12) {
  const indices = Array.from({ length: 24 }, (_, i) => start + i);
  return {
    id: `test-${scenario ?? "live"}-${revision}`,
    forecast: { scenario, source: scenario ? "scenario" : "fixture", sources: {}, generated_at: hours[start].time, now_index: 0, alert, best_window: null, hours: hours.slice(start, start + 24), note: "Test forecast" },
    hours: indices,
    values: indices.map((hour) => ({ hour, time: hours[hour].time, scenario, cai_base64: Buffer.from([80 - hour]).toString("base64"), background: 12 + hour + revision * 10, bias: 1, heat_weight: 0, stagnation: 30 })),
    sensors: { scenario, simulated: !!scenario, source: "fixture", count: 1, median_pm25: 12, label: "Test sensors", sensors: [{ id: 1, lat: 42.70, lon: 23.31, pm25: 12, pm10: 20, district: "Sredets" }] },
  };
}

export async function mockMapApi(page: Page) {
  let revision = 0;
  // Deterministic tiles: browser tests require neither an OSM connection nor API keys.
  await page.route("https://tile.openstreetmap.org/**", (route) => route.fulfill({
    contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAIAAADTED8xAAACv0lEQVR4nO3TMQ0AMAzAsPLn2mMMBqNHLBlAnsy+haw5L4BDBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxAmgFIMwBpBiDNAKQZgDQDkGYA0gxA2gcjvSnmKuFl2AAAAABJRU5ErkJggg==", "base64"),
  }));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const hour = Number(url.searchParams.get("hour") ?? 12);
    const scenario = url.searchParams.get("scenario");
    const mode = url.searchParams.get("mode") ?? "walk";
    let data: unknown;
    switch (url.pathname) {
      case "/api/forecast/window": data = forecastWindow(scenario, revision); break;
      case "/api/forecast/refresh": data = forecastWindow(scenario, ++revision); break;
      case "/api/forecast": data = { scenario, source: scenario ? "scenario" : "fixture", sources: {}, generated_at: time, now_index: 12, alert, best_window: null, hours, note: "Test forecast" }; break;
      case "/api/districts": data = { type: "FeatureCollection", features: [], meta: {} }; break;
      case "/api/sensors": data = { scenario, simulated: !!scenario, source: "fixture", count: 1, median_pm25: 12, label: "Test sensors", sensors: [{ id: 1, lat: 42.70, lon: 23.31, pm25: 12, pm10: 20, district: "Sredets" }] }; break;
      case "/api/reference": data = { enabled: true, attribution: "Test reference", pm25_available: false, note: "", stations: [{ uid: 1, name: "Test station", lat: 42.68, lon: 23.31, pm10: 18 }] }; break;
      case "/api/validation": data = { warning: "Synthetic browser test data" }; break;
      case "/api/traffic": data = { enabled: false, google: false, tomtom: false, ready: false, measured_profile: false, updated: null, corridors: [], profile: null, usage: { day: "", google_requests: 0, tomtom_requests: 0 }, note: "", sources: [] }; break;
      case "/api/traffic/incidents": data = { type: "FeatureCollection", features: [], enabled: false }; break;
      case "/api/google-air": data = { enabled: false }; break;
      case "/api/no2": data = { enabled: false }; break;
      case "/api/cai/geometry": {
        const buf = Buffer.alloc(8 + 8 + 16 + 1);
        buf.writeUInt32LE(1, 0); buf.writeUInt32LE(2, 4);
        buf.writeUInt32LE(0, 8); buf.writeUInt32LE(2, 12);
        line.flat().forEach((v, i) => buf.writeFloatLE(v, 16 + i * 4));
        buf[32] = 0;
        await route.fulfill({ contentType: "application/octet-stream", body: buf });
        return;
      }
      case "/api/cai/values": data = { hour, time: hours[hour].time, scenario, cai: [80 - hour], pm25: [12 + hour], background: 12 + hour, bias: 1, heat_weight: 0, stagnation: 30 }; break;
      case "/api/cai/edge": data = { id: 0, edge: 0, time: hours[hour].time, pm25: 12 + hour, cai: 80 - hour,
        street: "Demo Avenue", street_bg: "Демо", near_street: false, district: "Sredets", geometry: line, road_class: "primary", length_m: 500,
        components_ugm3: { background: 10, local_correction: 1, traffic: 3, ventilation: 2, greenery: -1 },
        drivers: { traffic_term: 3, rush: 1, amplifier: 1.5, greenery: 0.3, heat_anomaly: 0, heat_weight: 0, stagnation_score: 30 }, heat_points: 0, cai_air_only: 70, note: "" }; break;
      case "/api/route": {
        const from = (url.searchParams.get("from") ?? "42.69,23.29").split(",").map(Number);
        const to = (url.searchParams.get("to") ?? "42.69,23.35").split(",").map(Number);
        const path = [[from[1], from[0]], [to[1], to[0]]];
        const stats = { time_min: mode === "bike" ? 5 : 15, distance_m: 1000, mean_pm25: 12, dose: 180, mean_cai: 70, dose_change_pct: 0, time_change_pct: 0, lambda_used: 3, polyline: path };
        data = { from, to, from_label: "Start point", to_label: "End point", hour, time, scenario, mode,
          routes: { fastest: stats, balanced: stats, cleanest: stats, chosen: stats }, google_status: mode === "bike" ? "n/a" : "no_key", google_baseline: null,
          snapped: { used: false, polyline: path, eta_min: null, waypoints: [] }, open_in_google_maps: "https://www.google.com/maps", open_in_google_maps_clean: "https://www.google.com/maps", note: "" };
        break;
      }
      case "/api/network": data = { n_od: 400, hour, time, scenario, mode, median_dose_change_pct: -5, median_time_change_pct: 5,
        share_cleaner: 0.5, when_cleaner: { n: 200, median_dose_change_pct: -5, median_time_change_pct: 5 }, note: "", chokepoints: [choke],
        trips: [line], segments: [{ id: 1, path: line, usage_clean: 10, usage_fast: 2, cai: 70, channel: true }], channel_cai_cut: 60, n_channel_segments: 1 }; break;
      case "/api/cai/areas": {
        const metric = url.searchParams.get("metric") ?? "air";
        data = { metric, mode, cell_m: 3000, unit: "", hour, time, scenario, reference: 30,
          thresholds: { clean: 75, dirty: 25, hot: 2, cool: -2 }, stats: { median: 50, p10: 25, p90: 75, min: 20, max: 80 },
          cells: { lon: [23.32], lat: [42.69], mean: [metric === "air" ? 60 : 35], min: [30], max: [80], street_m: [1500], n: [10], pm25: [15], anomaly: [5], ...(metric === "heat" ? { air: [33], offset: [1.5] } : {}) },
          ...(metric === "heat" ? { scale: "absolute", centre: 18, span: 20, model: true, city: { feels: 30, air: 28, level: "caution", city_level: "caution" }, validation: { sensors: 220, sensor_hours: 570000 } } : {}),
          extremes: { best: [{ lon: 23.32, lat: 42.69, value: 60, street_m: 1500, place: "Demo park" }], worst: [] }, note: "Test areas" };
        break;
      }
      case "/api/advice": data = { cards: [] }; break;
      default: await route.fulfill({ status: 404, json: { detail: `Unmocked endpoint ${url.pathname}` } }); return;
    }
    await route.fulfill({ json: data });
  });
}

/** The Live menu holds the data scenarios, refresh and the map style. */
export async function openMenu(page: Page) {
  await page.getByRole("button", { name: /^(Live|Demo)/ }).click();
}
export async function pickProvider(page: Page, name: "Leaflet" | "MapLibre") {
  await openMenu(page);
  await page.getByRole("menuitemradio", { name: name === "Leaflet" ? "Leaflet · OSM" : "MapLibre · OpenFreeMap", exact: true }).click();
}
