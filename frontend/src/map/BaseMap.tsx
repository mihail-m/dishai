import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "leaflet/dist/leaflet.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { GoogleMapsOverlay } from "@deck.gl/google-maps";
import type { Layer, PickingInfo } from "@deck.gl/core";
import { GOOGLE_KEY, SOFIA_CENTER, preferredMapProvider, saveMapProvider, type MapProvider } from "./config";
import { googleLoader } from "./google";

maplibregl.setWorkerUrl(workerUrl);

export interface MapHandle {
  flyTo: (lng: number, lat: number, zoom?: number) => void;
  fitBounds: (w: number, s: number, e: number, n: number) => void;
}
interface Props {
  layers: Layer[];
  onClick?: (info: PickingInfo) => void;
  cursor?: string;
  tooltip?: (i: PickingInfo) => { html: string; style?: Record<string, string> } | null;
  active: boolean;
  handle?: Ref<MapHandle>;
}

// The shared camera uses deck.gl/MapLibre's 512px world tiles.
// Leaflet and Google use 256px tiles, so their zoom is one level higher.
type Camera = { lng: number; lat: number; zoom: number };
type Overlay = { setProps: (p: Record<string, unknown>) => void };
const INITIAL: Camera = { lng: SOFIA_CENTER[0], lat: SOFIA_CENTER[1], zoom: 11 };
const OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors';
// A finalized deck.gl Layer cannot be attached to another renderer. Keep the
// caller's layers uninitialized and give each overlay its own instances. IDs
// still let deck.gl reuse GPU state across updates within the same renderer.
const cloneLayers = (layers: Layer[]) => layers.map((layer) => layer.clone({}));

export default function BaseMap({ layers, onClick, cursor, tooltip, active, handle }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const overlay = useRef<Overlay | null>(null);
  const resize = useRef<() => void>(() => {});
  const move = useRef<MapHandle>({ flyTo: () => {}, fitBounds: () => {} });
  const camera = useRef<Camera>({ ...INITIAL });
  const pendingMove = useRef<((map: MapHandle) => void) | null>(null);
  const latest = useRef({ layers, onClick, cursor, tooltip });
  latest.current = { layers, onClick, cursor, tooltip };
  const [provider, setProvider] = useState<MapProvider>(preferredMapProvider);
  const [ready, setReady] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const readyRef = useRef(false);
  useImperativeHandle(handle, () => ({
    flyTo: (...args) => {
      if (readyRef.current) move.current.flyTo(...args);
      else pendingMove.current = (map) => map.flyTo(...args);
    },
    fitBounds: (...args) => {
      if (readyRef.current) move.current.fitBounds(...args);
      else pendingMove.current = (map) => map.fitBounds(...args);
    },
  }), []);

  const chooseProvider = (next: MapProvider) => {
    setNote(null);
    saveMapProvider(next);
    setProvider(next);
  };

  useEffect(() => {
    if (!el.current) return;
    const host = el.current;
    // Each renderer owns its own element, including during an asynchronous switch.
    const container = document.createElement("div");
    container.className = "map-canvas";
    host.appendChild(container);
    let disposed = false;
    let cleanup = () => {};
    readyRef.current = false;
    setReady(false);
    const deckProps = {
      onClick: (info: PickingInfo) => latest.current.onClick?.(info),
      getTooltip: (info: PickingInfo) => latest.current.tooltip?.(info) ?? null,
      getCursor: ({ isHovering, isDragging }: { isHovering: boolean; isDragging: boolean }) =>
        latest.current.cursor ?? (isDragging ? "grabbing" : isHovering ? "pointer" : "grab"),
    };
    const mounted = () => {
      // Data can arrive while a lazy renderer or Google Maps is still loading.
      overlay.current?.setProps({ layers: cloneLayers(latest.current.layers) });
      readyRef.current = true;
      setReady(true);
      resize.current();
      pendingMove.current?.(move.current);
      pendingMove.current = null;
    };

    const startMapLibre = () => {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const c = camera.current;
      const map = new maplibregl.Map({
        container, center: [c.lng, c.lat], zoom: c.zoom, attributionControl: { compact: true },
        style: {
          version: 8,
          sources: { osm: { type: "raster", tiles: [OSM], tileSize: 256, attribution: ATTRIBUTION, maxzoom: 18 } },
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
      cleanup = () => {
        const center = map.getCenter();
        camera.current = { lng: center.lng, lat: center.lat, zoom: map.getZoom() };
        map.remove();
      };
      mounted();
    };

    const startLeaflet = async () => {
      const [L, { DeckOverlay }] = await Promise.all([import("leaflet"), import("@deck.gl-community/leaflet")]);
      if (disposed) return;
      const c = camera.current;
      const map = L.map(container, {
        center: [c.lat, c.lng], zoom: c.zoom + 1, zoomControl: false,
        minZoom: 3, maxZoom: 19, zoomSnap: 0, zoomDelta: 0.5,
      });
      cleanup = () => {
        const center = map.getCenter();
        camera.current = { lng: center.lng, lat: center.lat, zoom: map.getZoom() - 1 };
        map.remove(); // also finalizes deck.gl and unregisters its map events
      };
      L.tileLayer(OSM, { attribution: ATTRIBUTION, maxNativeZoom: 19, maxZoom: 19 }).addTo(map);
      L.control.zoom({ position: "topright" }).addTo(map);
      L.control.scale({ position: "bottomright", imperial: false }).addTo(map);
      const ov = new DeckOverlay({ layers: [], ...deckProps });
      ov.addTo(map);
      overlay.current = ov;
      resize.current = () => map.invalidateSize({ pan: false });
      move.current = {
        flyTo: (lng, lat, zoom = 14) => { map.flyTo([lat, lng], Math.min(19, zoom + 1), { duration: 0.9 }); },
        fitBounds: (w, s, e, n) => { map.flyToBounds([[s, w], [n, e]], { padding: [60, 60], maxZoom: 18, duration: 0.9 }); },
      };
      mounted();
    };

    const startGoogle = async () => {
      const { importLibrary } = await googleLoader();
      const { Map } = (await importLibrary("maps")) as google.maps.MapsLibrary;
      if (disposed) return;
      const c = camera.current;
      const map = new Map(container, {
        center: { lat: c.lat, lng: c.lng }, zoom: c.zoom + 1, gestureHandling: "greedy", clickableIcons: false,
        mapId: "DEMO_MAP_ID", disableDefaultUI: false,
      });
      const ov = new GoogleMapsOverlay({ layers: [], ...deckProps });
      ov.setMap(map);
      overlay.current = ov;
      resize.current = () => google.maps.event.trigger(map, "resize");
      move.current = {
        flyTo: (lng, lat, zoom = 14) => { map.panTo({ lat, lng }); map.setZoom(zoom + 1); },
        fitBounds: (w, s, e, n) => map.fitBounds({ west: w, south: s, east: e, north: n }, 60),
      };
      cleanup = () => {
        const center = map.getCenter();
        if (center) camera.current = { lng: center.lng(), lat: center.lat(), zoom: (map.getZoom() ?? 12) - 1 };
        ov.finalize();
        google.maps.event.clearInstanceListeners(map);
      };
      mounted();
    };

    const start = async () => {
      try {
        if (provider === "leaflet") await startLeaflet();
        else if (provider === "google") await startGoogle();
        else startMapLibre();
      } catch (error) {
        if (disposed) return;
        console.warn(`${provider} map unavailable`, error);
        if (provider !== "maplibre") {
          setNote(`${provider === "leaflet" ? "Leaflet" : "Google Maps"} unavailable. Using MapLibre with OpenStreetMap.`);
          setProvider("maplibre");
        } else setNote("The map could not start. Please reload or try Leaflet.");
      }
    };
    void start();
    const observer = new ResizeObserver(() => resize.current());
    observer.observe(host);
    return () => {
      disposed = true;
      observer.disconnect();
      readyRef.current = false;
      cleanup();
      overlay.current = null;
      resize.current = () => {};
      container.remove();
    };
  }, [provider]);

  useEffect(() => { overlay.current?.setProps({ layers: cloneLayers(layers) }); }, [layers, provider]);
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => resize.current(), 30);
    return () => clearTimeout(timer);
  }, [active]);

  return (
    <div className="map-shell" data-map-provider={provider} data-map-ready={ready}>
      <div className="map-toolbar">
        <div className="map-style-options" role="group" aria-label="Map renderer">
          <span className="map-style-label">Map style</span>
          <button aria-pressed={provider === "leaflet"} onClick={() => chooseProvider("leaflet")} title="Leaflet 1.9.4 with OpenStreetMap">Leaflet · OSM</button>
          <button aria-pressed={provider === "maplibre"} onClick={() => chooseProvider("maplibre")}>MapLibre · OSM</button>
          {GOOGLE_KEY && <button aria-pressed={provider === "google"} onClick={() => chooseProvider("google")}>Google Maps</button>}
        </div>
        <button className="map-reset" onClick={() => move.current.flyTo(INITIAL.lng, INITIAL.lat, INITIAL.zoom)} disabled={!ready} title="Return to the Sofia overview">Reset view</button>
      </div>
      <div className="map-viewport" ref={el} role="application" aria-label="Map of Sofia street-level Clean Air Index" />
      {!ready && !note && <div className="map-notice" role="status">Loading map…</div>}
      {note && <div className="map-notice" role="status">{note}</div>}
    </div>
  );
}
