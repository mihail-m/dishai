import type { Profile, Species, Toggle } from "./types";

export const TOGGLE_IDS: Toggle[] = ["pollen_allergy", "asthma", "heat_sensitive", "outdoor_exercise", "kids_or_elderly"];
export const SPECIES_IDS: Species[] = ["alder", "birch", "grass", "mugwort", "olive", "ragweed"];
export const SPECIES_LABEL: Record<Species, string> = { alder: "Alder", birch: "Birch", grass: "Grass", mugwort: "Mugwort", olive: "Olive", ragweed: "Ragweed" };
export const SPECIES_KIND: Record<Species, string> = { alder: "tree", birch: "tree", grass: "grass", mugwort: "weed", olive: "tree", ragweed: "weed" };
const KEY = "dishai.foryou";

export interface Prefs { profile?: Profile; toggles: Toggle[]; species: Species[] }

/** "For me" choices, kept in this browser only. Nothing here is sent anywhere except as query parameters on the advice and route calls. */
export function loadPrefs(): Prefs {
  try {
    const p = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return {
      profile: p.profile,
      toggles: Array.isArray(p.toggles) ? p.toggles.filter((t: string): t is Toggle => (TOGGLE_IDS as string[]).includes(t)) : [],
      species: Array.isArray(p.species) ? p.species.filter((t: string): t is Species => (SPECIES_IDS as string[]).includes(t)) : [],
    };
  } catch { return { toggles: [], species: [] }; }
}

export function savePrefs(p: { profile?: Profile; toggles: Toggle[]; species: Species[] }) {
  try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* private mode: preferences just aren't remembered */ }
}
