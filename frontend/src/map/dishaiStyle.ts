// The DishAI basemap: OpenFreeMap's vector "positron" style, recoloured in a calm green-tinted neutral palette, with the clutter removed
// (one name per place instead of Cyrillic plus Latin, no small suburb labels, shields or airport icons), so the data layer carries the colour.
type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const PALETTE = {
  light: {
    bg: "#f1f3ee", park: "#e3ecdc", wood: "#dce7d4", residential: "#edeee8", water: "#cfe0e7", waterway: "#c4d7df", building: "#e8e9e1",
    minor: "#d9ddd1", path: "#dfe3d8", majorCasing: "#c9cfc0", majorInner: "#ffffff", majorSubtle: "#d3d8cb", motoCasing: "#dacfb6", motoInner: "#f7edd3",
    rail: "#d6d9cf", boundary: "#b9c2b2", aero: "#e4e6de", text: "#46574c", halo: "#f1f3ee", place: "#2b3b31", water_text: "#5d7f8c",
  },
  dark: {
    bg: "#0e1612", park: "#13211a", wood: "#12201a", residential: "#111b16", water: "#12232b", waterway: "#16282f", building: "#17231c",
    minor: "#26372d", path: "#22322a", majorCasing: "#0e1612", majorInner: "#3b4d41", majorSubtle: "#2c3d33", motoCasing: "#0e1612", motoInner: "#3a3b2c",
    rail: "#25332b", boundary: "#3a4a3f", aero: "#1b2922", text: "#92a69a", halo: "#0e1612", place: "#c5d6ca", water_text: "#6f9aa8",
  },
} as const;

const HIDE = new Set(["label_other", "airport", "highway-shield-non-us", "highway-shield-us-interstate", "road_shield_us", "highway-name-path", "waterway_line_label"]);
const NAME = ["coalesce", ["get", "name:latin"], ["get", "name"]];

export function restyle(style: Json, dark: boolean): Json {
  const c = PALETTE[dark ? "dark" : "light"];
  const paint: Record<string, Json> = {
    background: { "background-color": c.bg }, park: { "fill-color": c.park }, landcover_wood: { "fill-color": c.wood },
    landuse_residential: { "fill-color": c.residential }, water: { "fill-color": c.water }, waterway: { "line-color": c.waterway },
    building: { "fill-color": c.building, "fill-opacity": 0.7 }, "aeroway-taxiway": { "line-color": c.aero }, "aeroway-runway-casing": { "line-color": c.aero },
    "aeroway-area": { "fill-color": c.aero }, "aeroway-runway": { "line-color": c.majorInner }, highway_path: { "line-color": c.path },
    highway_minor: { "line-color": c.minor }, highway_major_casing: { "line-color": c.majorCasing }, highway_major_inner: { "line-color": c.majorInner },
    highway_major_subtle: { "line-color": c.majorSubtle }, highway_motorway_subtle: { "line-color": c.majorSubtle },
    highway_motorway_casing: { "line-color": c.motoCasing }, highway_motorway_inner: { "line-color": c.motoInner },
    highway_motorway_bridge_casing: { "line-color": c.motoCasing }, highway_motorway_bridge_inner: { "line-color": c.motoInner },
    tunnel_motorway_casing: { "line-color": c.majorCasing }, tunnel_motorway_inner: { "line-color": c.minor },
    boundary_2: { "line-color": c.boundary }, boundary_3: { "line-color": c.boundary }, boundary_disputed: { "line-color": c.boundary },
  };
  for (const l of style.layers as Json[]) {
    if (HIDE.has(l.id)) { l.layout = { ...l.layout, visibility: "none" }; continue; }
    if (paint[l.id]) l.paint = { ...l.paint, ...paint[l.id] };
    else if (l.id.startsWith("railway")) l.paint = { ...l.paint, "line-color": l.id.endsWith("dashline") ? c.bg : c.rail };
    if (l.type === "symbol") {
      const water = l.id.startsWith("water");
      l.paint = { ...l.paint, "text-color": water ? c.water_text : l.id.startsWith("label_") ? c.place : c.text, "text-halo-color": c.halo, "text-halo-width": 1.4 };
      if (l.layout?.["text-field"]) l.layout = { ...l.layout, "text-field": NAME };
    }
  }
  return style;
}

const cache = new Map<boolean, Promise<Json>>();
/** Fetch and restyle the basemap; resolves to the plain style URL if the fetch fails so the map still starts. */
export function loadBasemapStyle(dark: boolean): Promise<Json | string> {
  const url = "https://tiles.openfreemap.org/styles/positron";
  let p = cache.get(dark);
  if (!p) { p = fetch(url).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); }).then((j) => restyle(j, dark)); cache.set(dark, p); }
  return p.catch(() => { cache.delete(dark); return url; });
}
