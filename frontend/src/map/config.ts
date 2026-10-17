const key = import.meta.env.GOOGLE_MAPS_BROWSER_KEY || "";
const wanted = (import.meta.env.MAP_PROVIDER || "").toLowerCase();

// Google only when asked for (or the default with a key) AND a key exists; otherwise MapLibre + OSM tiles.
export const MAP_PROVIDER: "google" | "maplibre" =
  key && (wanted === "google" || (wanted === "" && key)) ? "google" : "maplibre";
export const GOOGLE_KEY = key;
export const SOFIA_CENTER: [number, number] = [23.32, 42.69];

export const PRESET_PAIRS = [
  { id: "central", label: "Central: Sofia University → Central Station", from: [42.6934, 23.3331], to: [42.712, 23.323] },
  { id: "across", label: "Across the city: Lyulin → Mladost", from: [42.7186, 23.2543], to: [42.656, 23.383] },
  { id: "suburban", label: "Suburban: Boyana → Dragalevtsi", from: [42.6445, 23.264], to: [42.631, 23.309] },
] as const;
