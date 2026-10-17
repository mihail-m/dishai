import { useEffect, useRef } from "react";

/** Google Places search (new PlaceAutocompleteElement). Only rendered when MAP_PROVIDER=google. */
export default function PlaceSearch({ onPick }: { onPick: (latlng: [number, number]) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const cb = useRef(onPick);
  cb.current = onPick;
  useEffect(() => {
    let el: HTMLElement | null = null;
    let dead = false;
    (async () => {
      try {
        const places = (await google.maps.importLibrary("places")) as google.maps.PlacesLibrary;
        if (dead || !host.current) return;
        const ac = new places.PlaceAutocompleteElement({ includedRegionCodes: ["bg"] });
        el = ac as unknown as HTMLElement;
        host.current.appendChild(el);
        el.addEventListener("gmp-select", async (ev: Event) => {
          const place = (ev as unknown as { placePrediction: { toPlace: () => google.maps.places.Place } }).placePrediction.toPlace();
          await place.fetchFields({ fields: ["location"] });
          if (place.location) cb.current([place.location.lat(), place.location.lng()]);
        });
      } catch (e) {
        console.warn("Places unavailable", e);
      }
    })();
    return () => { dead = true; el?.remove(); };
  }, []);
  return <div ref={host} className="text-sm" aria-label="Search a place (sets the active point)" />;
}
