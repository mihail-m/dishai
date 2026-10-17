const key = import.meta.env.GOOGLE_MAPS_BROWSER_KEY || "";
const wanted = (import.meta.env.MAP_PROVIDER || "").toLowerCase();

export type MapProvider = "google" | "maplibre" | "leaflet";
// Keep the existing default; Leaflet can be selected in the UI or via .env.
export const MAP_PROVIDER: MapProvider = wanted === "leaflet" ? "leaflet"
  : key && (wanted === "google" || wanted === "") ? "google" : "maplibre";
export const GOOGLE_KEY = key;
export const SOFIA_CENTER: [number, number] = [23.32, 42.69];

const PREFERENCE = "dishai.map-provider";
export function preferredMapProvider(): MapProvider {
  try {
    const saved = localStorage.getItem(PREFERENCE);
    if (saved === "leaflet" || saved === "maplibre" || (saved === "google" && key)) return saved;
  } catch { /* Storage may be unavailable in private/embedded browsers. */ }
  return MAP_PROVIDER;
}
export function saveMapProvider(provider: MapProvider): void {
  try { localStorage.setItem(PREFERENCE, provider); } catch { /* Selection still works without storage. */ }
}

export const PRESET_PAIRS = [
  { id: "central", label: "Central: Sofia University → Central Station", from: [42.6934, 23.3331], to: [42.712, 23.323] },
  { id: "across", label: "Across the city: Lyulin → Mladost", from: [42.7186, 23.2543], to: [42.656, 23.383] },
  { id: "suburban", label: "Suburban: Boyana → Dragalevtsi", from: [42.6445, 23.264], to: [42.631, 23.309] },
] as const;
