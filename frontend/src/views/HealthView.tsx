import { useState } from "react";
import type { DistrictsFC, Scenario, Species, Toggle } from "../types";
import { loadPrefs } from "../prefs";
import ForMeView from "./ForMeView";
import AllergyView from "./AllergyView";

interface Props { scenario: Scenario; districts: DistrictsFC | null; district: string | null; setDistrict: (d: string) => void }

/** "My health": the personal verdict and plan on top, the pollen and allergy detail below. The choices are made once, in the "Tailor this to you" card. */
export default function HealthView({ scenario, districts, district, setDistrict }: Props) {
  const [mine, setMine] = useState<Species[]>(() => { const p = loadPrefs(); return p.toggles.includes("pollen_allergy") ? p.species : []; });
  // plants only count while "Pollen allergy" is on
  const onPrefs = (toggles: Toggle[], species: string[]) => setMine(toggles.includes("pollen_allergy") ? (species as Species[]) : []);
  return (
    <div className="mx-auto grid max-w-[680px] gap-8">
      <ForMeView scenario={scenario} districts={districts} district={district} setDistrict={setDistrict} onPrefs={onPrefs} />
      <AllergyView scenario={scenario} district={district} mine={mine} />
    </div>
  );
}
