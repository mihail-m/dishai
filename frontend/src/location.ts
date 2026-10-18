import { useCallback, useEffect, useRef, useState } from "react";
import type { DistrictsFC } from "./types";

export interface Place { lat: number; lon: number; accuracy: number }
export type LocStatus = "idle" | "asking" | "ok" | "denied" | "unavailable";

/** The user's position, asked for only when they press a button and kept in memory only: nothing is stored or sent. */
export function useLocation() {
  const [status, setStatus] = useState<LocStatus>("idle");
  const [place, setPlace] = useState<Place | null>(null);
  const watch = useRef<number | null>(null);
  const request = useCallback(() => {
    if (!("geolocation" in navigator)) { setStatus("unavailable"); return; }
    if (watch.current != null) return;
    setStatus("asking");
    watch.current = navigator.geolocation.watchPosition(
      (p) => { setPlace({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }); setStatus("ok"); },
      (e) => { setStatus(e.code === e.PERMISSION_DENIED ? "denied" : "unavailable"); if (watch.current != null) navigator.geolocation.clearWatch(watch.current); watch.current = null; },
      { enableHighAccuracy: false, maximumAge: 60_000, timeout: 20_000 },
    );
  }, []);
  const stop = useCallback(() => {
    if (watch.current != null) navigator.geolocation.clearWatch(watch.current);
    watch.current = null; setPlace(null); setStatus("idle");
  }, []);
  useEffect(() => () => { if (watch.current != null) navigator.geolocation.clearWatch(watch.current); }, []);
  return { status, place, request, stop };
}

export function distanceKm(aLat: number, aLon: number, bLat: number, bLon: number) {
  const r = Math.PI / 180, dLat = (bLat - aLat) * r, dLon = (bLon - aLon) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(aLat * r) * Math.cos(bLat * r) * Math.sin(dLon / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
}

type Ring = number[][];
const inRing = (lon: number, lat: number, ring: Ring) => {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
};

/** Name of the city district the point falls in, or null outside them. */
export function districtAt(fc: DistrictsFC | null, lat: number, lon: number): string | null {
  for (const f of fc?.features ?? []) {
    const g = f.geometry;
    const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
    if (polys.some((p) => inRing(lon, lat, p[0]) && !p.slice(1).some((h) => inRing(lon, lat, h)))) return f.properties.name;
  }
  return null;
}
