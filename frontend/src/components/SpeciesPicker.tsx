import type { Species } from "../types";
import { SPECIES_IDS, SPECIES_KIND, SPECIES_LABEL } from "../prefs";

/** Which pollen the person reacts to. Empty means "all of them". */
export default function SpeciesPicker({ value, onChange }: { value: Species[]; onChange: (s: Species[]) => void }) {
  const flip = (s: Species) => onChange(value.includes(s) ? value.filter((x) => x !== s) : [...value, s]);
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Pollen I react to">
      {SPECIES_IDS.map((s) => (
        <button key={s} className={`chip ${value.includes(s) ? "on" : ""}`} aria-pressed={value.includes(s)} title={`${SPECIES_LABEL[s]} (${SPECIES_KIND[s]})`} onClick={() => flip(s)}>{SPECIES_LABEL[s]}</button>
      ))}
      {value.length > 0 && <button className="chip" onClick={() => onChange([])}>All pollen</button>}
    </div>
  );
}
