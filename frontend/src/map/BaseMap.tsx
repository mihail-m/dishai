import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "leaflet/dist/leaflet.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { GoogleMapsOverlay } from "@deck.gl/google-maps";
import type { Layer, PickingInfo } from "@deck.gl/core";
import { SOFIA_CENTER, type MapProvider } from "./config";
import type { Animated } from "./surface";
import { loadBasemapStyle } from "./dishaiStyle";
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
  /** drawn every frame: soft surfaces that morph and pings that pulse, under and over the static layers */
  animated?: Animated;
  provider: MapProvider;
  dark: boolean;
  onProviderChange: (p: MapProvider) => void;
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

export default function BaseMap({ layers, onClick, cursor, tooltip, active, handle, animated, provider, dark, onProviderChange }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const overlay = useRef<Overlay | null>(null);
  const resize = useRef<() => void>(() => {});
  const move = useRef<MapHandle>({ flyTo: () => {}, fitBounds: () => {} });
  const camera = useRef<Camera>({ ...INITIAL });
  const pendingMove = useRef<((map: MapHandle) => void) | null>(null);
  const latest = useRef({ layers, onClick, cursor, tooltip, animated });
  latest.current = { layers, onClick, cursor, tooltip, animated };
  const applied = useRef(false); // the last frame drawn had nothing moving
  const compose = (now = performance.now()) => {
    const a = latest.current.animated?.(now);
    applied.current = a ? a.settled : true;
    return cloneLayers([...(a?.under ?? []), ...latest.current.layers, ...(a?.over ?? [])]);
  };
  const composeRef = useRef(compose);
  composeRef.current = compose;
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

  const fellBack = useRef(false); // keep the 'unavailable' note when the provider switch was our own fallback

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
    if (!fellBack.current) setNote(null);
    fellBack.current = false;
    const deckProps = {
      onClick: (info: PickingInfo) => latest.current.onClick?.(info),
      getTooltip: (info: PickingInfo) => latest.current.tooltip?.(info) ?? null,
      getCursor: ({ isHovering, isDragging }: { isHovering: boolean; isDragging: boolean }) =>
        latest.current.cursor ?? (isDragging ? "grabbing" : isHovering ? "pointer" : "grab"),
    };
    const mounted = () => {
      // Data can arrive while a lazy renderer or Google Maps is still loading.
      overlay.current?.setProps({ layers: composeRef.current() });
      readyRef.current = true;
      setReady(true);
      resize.current();
      pendingMove.current?.(move.current);
      pendingMove.current = null;
    };

    const startMapLibre = async () => {
      const style = await loadBasemapStyle(dark);
      if (disposed) return;
      const c = camera.current;
      const map = new maplibregl.Map({
        container, center: [c.lng, c.lat], zoom: c.zoom, attributionControl: { compact: true },
        style: style as never,
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
        else await startMapLibre();
      } catch (error) {
        if (disposed) return;
        console.warn(`${provider} map unavailable`, error);
        if (provider !== "maplibre") {
          setNote(`${provider === "leaflet" ? "Leaflet" : "Google Maps"} unavailable. Using MapLibre with OpenStreetMap.`);
          fellBack.current = true;
          onProviderChange("maplibre");
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
  }, [provider, provider === "maplibre" ? dark : null]); // only the MapLibre basemap follows the theme; the camera is kept across the restart

  useEffect(() => { overlay.current?.setProps({ layers: composeRef.current() }); }, [layers, animated, provider]);
  useEffect(() => {
    let raf = 0, last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (!latest.current.animated || !overlay.current || !readyRef.current || now - last < 60) return;
      last = now;
      const frame = latest.current.animated(now);
      if (frame.settled && applied.current) return; // nothing is moving: leave the last frame on screen
      applied.current = frame.settled;
      overlay.current.setProps({ layers: cloneLayers([...frame.under, ...latest.current.layers, ...frame.over]) });
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => resize.current(), 30);
    return () => clearTimeout(timer);
  }, [active]);

  return (
    <div className="map-shell" data-map-provider={provider} data-map-ready={ready}>
      <div className="map-viewport" ref={el} role="application" aria-label="Map of Sofia air quality" />
      <button className="map-reset" onClick={() => move.current.flyTo(INITIAL.lng, INITIAL.lat, INITIAL.zoom)} disabled={!ready} title="Back to the Sofia overview" aria-label="Reset view">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg>
      </button>
      {!ready && !note && <div className="map-notice" role="status">Loading map…</div>}
      {note && <div className="map-notice" role="status">{note}</div>}
    </div>
  );
}
