import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { GoogleMapsOverlay } from "@deck.gl/google-maps";
import type { Layer, PickingInfo } from "@deck.gl/core";
import { GOOGLE_KEY, MAP_PROVIDER, SOFIA_CENTER } from "./config";

maplibregl.setWorkerUrl(workerUrl);

export interface MapHandle {
  flyTo: (lng: number, lat: number, zoom?: number) => void;
  fitBounds: (w: number, s: number, e: number, n: number) => void;
}
interface Props {
  layers: Layer[];
  onClick?: (info: PickingInfo) => void;
  cursor?: string;
  active: boolean; // false when the tab hides the map (it stays mounted to keep the GPU buffers)
  handle?: Ref<MapHandle>;
}

// Deck.gl overlay shared by both providers: only `setProps` and `finalize` are needed.
type Overlay = { setProps: (p: Record<string, unknown>) => void; finalize?: () => void };

export default function BaseMap({ layers, onClick, cursor, active, handle }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const overlay = useRef<Overlay | null>(null);
  const resize = useRef<() => void>(() => {});
  const move = useRef<MapHandle>({ flyTo: () => {}, fitBounds: () => {} });
  const clickRef = useRef(onClick);
  clickRef.current = onClick;
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  const [provider, setProvider] = useState<"google" | "maplibre">(MAP_PROVIDER);
  const [note, setNote] = useState<string | null>(null);
  useImperativeHandle(handle, () => ({
    flyTo: (...a) => move.current.flyTo(...a),
    fitBounds: (...a) => move.current.fitBounds(...a),
  }));

  useEffect(() => {
    if (!el.current) return;
    const host = el.current;
    let disposed = false;
    let cleanup = () => {};
    const deckProps = {
      onClick: (info: PickingInfo) => clickRef.current?.(info),
      getCursor: ({ isHovering, isDragging }: { isHovering: boolean; isDragging: boolean }) =>
        cursorRef.current ?? (isDragging ? "grabbing" : isHovering ? "pointer" : "grab"),
    };

    const startMapLibre = () => {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const map = new maplibregl.Map({
        container: host, center: SOFIA_CENTER, zoom: 11, attributionControl: { compact: true },
        style: {
          version: 8,
          sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors", maxzoom: 18 } },
          layers: [
            { id: "bg", type: "background", paint: { "background-color": dark ? "#1b2420" : "#e8ece9" } },
            { id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": dark ? 0.28 : 0.55, "raster-saturation": -0.85, "raster-brightness-max": dark ? 0.5 : 1 } },
          ],
        },
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      const ov = new MapboxOverlay({ interleaved: false, layers: [], ...deckProps });
      map.addControl(ov as unknown as maplibregl.IControl);
      overlay.current = ov;
      resize.current = () => map.resize();
      move.current = {
        flyTo: (lng, lat, zoom = 14) => map.flyTo({ center: [lng, lat], zoom, duration: 900 }),
        fitBounds: (w, s, e, n) => map.fitBounds([[w, s], [e, n]], { padding: 60, duration: 900 }),
      };
      cleanup = () => { overlay.current = null; map.remove(); };
    };

    const startGoogle = async () => {
      try {
        const { setOptions, importLibrary } = await import("@googlemaps/js-api-loader");
        setOptions({ key: GOOGLE_KEY, v: "weekly" });
        const { Map } = (await importLibrary("maps")) as google.maps.MapsLibrary;
        if (disposed) return;
        const map = new Map(host, {
          center: { lat: SOFIA_CENTER[1], lng: SOFIA_CENTER[0] }, zoom: 11, gestureHandling: "greedy", clickableIcons: false,
          mapId: "DEMO_MAP_ID", disableDefaultUI: false,
        });
        const ov = new GoogleMapsOverlay({ layers: [], ...deckProps });
        ov.setMap(map);
        overlay.current = ov;
        resize.current = () => google.maps.event.trigger(map, "resize");
        move.current = {
          flyTo: (lng, lat, zoom = 14) => { map.panTo({ lat, lng }); map.setZoom(zoom); },
          fitBounds: (w, s, e, n) => map.fitBounds({ west: w, south: s, east: e, north: n }, 60),
        };
        cleanup = () => { overlay.current = null; ov.finalize?.(); };
      } catch (err) {
        // never fail the demo because Google is unreachable or the key/quota is bad
        console.warn("Google Maps unavailable, falling back to MapLibre", err);
        if (!disposed) { setNote("Google Maps unavailable, using OpenStreetMap."); setProvider("maplibre"); }
      }
    };

    if (provider === "google") startGoogle();
    else startMapLibre();
    return () => { disposed = true; cleanup(); host.innerHTML = ""; };
  }, [provider]);

  useEffect(() => { overlay.current?.setProps({ layers }); }, [layers, provider]);
  useEffect(() => { if (active) setTimeout(() => resize.current(), 30); }, [active]);

  return (
    <div className="relative h-full w-full">
      <div ref={el} style={{ width: "100%", height: "100%" }} role="application" aria-label="Map of Sofia street-level Clean Air Index" />
      {note && <div className="card absolute left-3 top-3 z-10 px-3 py-1.5 text-xs">{note}</div>}
    </div>
  );
}
