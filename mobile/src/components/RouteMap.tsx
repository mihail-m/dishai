import { useCallback, useEffect, useRef } from 'react';
import L from 'leaflet';
import { LocateFixed, Minus, Plus } from 'lucide-react';
import 'leaflet/dist/leaflet.css';
import '../map.css';
import sofia from '../data/sofia-map.json';
import type { DataMode, RouteData, RouteId, Sensor } from '../types';

type Coordinate = [number, number];

export interface RouteMapProps {
  selectedRoute: RouteId;
  routes?: RouteData | null;
  selectedHour?: number;
  pollutionVisible?: boolean;
  mode?: DataMode;
  sensors?: Sensor[];
  /** True while the route comparison sheet is expanded over the map. */
  compact?: boolean;
  /** Pixels covered by the bottom sheet and navigation; defaults follow the demo layout. */
  bottomInset?: number;
  onSelectRoute?: (route: RouteId) => void;
  className?: string;
}

const PALETTE = { clean: '#6564ed', fast: '#e99a66' };
const DEMO_ROUTES = Object.fromEntries(
  sofia.features
    .filter((feature) => feature.properties.kind === 'route')
    .map((feature) => [
      feature.properties.route === 'low' ? 'clean' : 'fast',
      feature.geometry.coordinates as Coordinate[],
    ]),
) as Record<RouteId, Coordinate[]>;

const LABELS = [
  { point: [42.69482, 23.32535], text: 'CITY GARDEN', kind: 'park' },
  { point: [42.6905, 23.3341], text: 'PRINCE’S GARDEN', kind: 'park' },
  { point: [42.69448, 23.3294], text: 'CRYSTAL GARDEN', kind: 'park' },
  { point: [42.69608, 23.33295], text: 'ALEXANDER NEVSKY', kind: 'landmark' },
  { point: [42.69655, 23.3224], text: 'SOFIA CENTRE', kind: 'district' },
  { point: [42.69487, 23.3304], text: 'Tsar Osvoboditel Blvd', kind: 'street', angle: -28 },
  { point: [42.6927, 23.3288], text: 'Gurko St', kind: 'street', angle: -31 },
  { point: [42.69272, 23.3332], text: 'Vasil Levski Blvd', kind: 'street', angle: 43 },
  { point: [42.69225, 23.3266], text: 'Rakovski St', kind: 'street', angle: 44 },
];

function latLngs(coordinates: Coordinate[]): L.LatLngTuple[] {
  return coordinates
    .filter(([lon, lat]) => Number.isFinite(lon) && Number.isFinite(lat))
    .map(([lon, lat]) => [lat, lon]);
}

function pointMarker(point: L.LatLngExpression, role: 'start' | 'end') {
  return L.marker(point, {
    interactive: false,
    zIndexOffset: 400,
    icon: L.divIcon({
      className: 'route-map-endpoint-anchor',
      html: `<span class="route-map-endpoint route-map-endpoint--${role}"><span>${role === 'start' ? 'A' : 'B'}</span></span>`,
      iconSize: [34, 34],
      iconAnchor: [17, 17],
    }),
  });
}

/** An entirely local OSM street map. Network data is only drawn when supplied by the parent. */
export default function RouteMap({
  selectedRoute,
  routes,
  selectedHour = 0,
  pollutionVisible = true,
  mode = 'demo',
  sensors = [],
  compact = true,
  bottomInset,
  onSelectRoute,
  className = '',
}: RouteMapProps) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const routeLayer = useRef<L.LayerGroup | null>(null);
  const airLayer = useRef<L.LayerGroup | null>(null);
  const routeBounds = useRef<L.LatLngBounds | null>(null);
  const selection = useRef(onSelectRoute);
  const firstFit = useRef(false);
  const lastSize = useRef({ width: 0, height: 0 });
  const layout = useRef({ compact, bottomInset });
  selection.current = onSelectRoute;
  layout.current = { compact, bottomInset };

  const fit = useCallback(() => {
    const instance = map.current;
    if (!instance) return;
    const bounds = routeBounds.current;
    if (bounds?.isValid()) {
      const height = instance.getSize().y;
      const top = Math.min(108, Math.max(30, height - 200));
      const requested = layout.current.bottomInset ?? (layout.current.compact ? 480 : 250);
      const bottom = Math.min(requested, Math.max(0, height - top - 170));
      instance.fitBounds(bounds, {
        paddingTopLeft: [35, top],
        paddingBottomRight: [50, bottom],
        maxZoom: 16.5,
        animate: false,
      });
    } else {
      instance.setView([42.694, 23.3287], 15.75, { animate: false });
    }
  }, []);

  useEffect(() => {
    if (!host.current) return;
    const instance = L.map(host.current, {
      center: [42.694, 23.3287],
      zoom: 15.75,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      minZoom: 14,
      maxZoom: 18.5,
      zoomControl: false,
      attributionControl: true,
      scrollWheelZoom: false,
      // Leave room for an off-centre camera while the bottom sheet covers most of the viewport.
      maxBounds: [[42.67, 23.28], [42.72, 23.37]],
      maxBoundsViscosity: 0.8,
    });
    map.current = instance;
    instance.attributionControl.setPrefix(false);
    instance.attributionControl.setPosition('topright');
    instance.attributionControl.addAttribution('<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>');

    instance.createPane('parkPane').style.zIndex = '200';
    instance.createPane('roadPane').style.zIndex = '220';
    instance.createPane('airPane').style.zIndex = '230';
    instance.getPane('airPane')!.style.pointerEvents = 'none';
    instance.createPane('routePane').style.zIndex = '430';
    // A handful of merged SVG paths is fast and avoids Leaflet 1.9.4's canvas
    // redraw callbacks surviving map teardown during screen changes.
    const parkRenderer = L.svg({ pane: 'parkPane' });
    const roadRenderer = L.svg({ pane: 'roadPane' });

    const parks = { type: 'FeatureCollection', features: sofia.features.filter((feature) => feature.properties.kind === 'park') };
    const roadGroups = new Map<string, Coordinate[][]>();
    for (const feature of sofia.features.filter((feature) => feature.properties.kind === 'road')) {
      const highway = feature.properties.highway ?? 'residential';
      const kind = ['primary', 'secondary', 'tertiary'].includes(highway) ? 'primary'
        : ['footway', 'path', 'steps'].includes(highway) ? 'footway' : 'residential';
      const group = roadGroups.get(kind) ?? [];
      group.push(feature.geometry.coordinates as Coordinate[]);
      roadGroups.set(kind, group);
    }
    const roads = {
      type: 'FeatureCollection',
      features: [...roadGroups].map(([highway, coordinates]) => ({
        type: 'Feature', properties: { highway }, geometry: { type: 'MultiLineString', coordinates },
      })),
    };
    const parkFeatures = L.geoJSON(parks as Parameters<typeof L.geoJSON>[0], {
      interactive: false,
      style: { renderer: parkRenderer, fillColor: '#c3e7d5', color: '#b8ddcb', weight: 1, fillOpacity: 0.85 },
    }).addTo(instance);
    const roadEdges = L.geoJSON(roads as Parameters<typeof L.geoJSON>[0], {
      interactive: false,
      style: (feature) => {
        const main = ['primary', 'secondary', 'tertiary'].includes(feature?.properties.highway);
        const path = ['footway', 'path', 'steps'].includes(feature?.properties.highway);
        return { renderer: roadRenderer, color: path ? '#d0dcd9' : '#d3dfe0', weight: main ? 12 : path ? 3 : 7, opacity: path ? 0.7 : 0.95, lineCap: 'round' };
      },
    }).addTo(instance);
    const roadCenters = L.geoJSON(roads as Parameters<typeof L.geoJSON>[0], {
      interactive: false,
      style: (feature) => {
        const main = ['primary', 'secondary', 'tertiary'].includes(feature?.properties.highway);
        const path = ['footway', 'path', 'steps'].includes(feature?.properties.highway);
        return { renderer: roadRenderer, color: path ? '#f1f6ef' : '#ffffff', weight: main ? 9 : path ? 1.5 : 4.5, opacity: 0.96, lineCap: 'round' };
      },
    }).addTo(instance);

    for (const label of LABELS) {
      L.marker(label.point as L.LatLngTuple, {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: 'route-map-label-anchor',
          html: `<span class="route-map-label route-map-label--${label.kind}" style="--label-angle:${label.angle ?? 0}deg">${label.text}</span>`,
          iconSize: [0, 0],
        }),
      }).addTo(instance);
    }

    routeLayer.current = L.layerGroup().addTo(instance);
    airLayer.current = L.layerGroup().addTo(instance);
    const observer = new ResizeObserver(() => {
      instance.invalidateSize({ pan: false });
      const size = instance.getSize();
      if (size.x > 0 && (!firstFit.current || lastSize.current.width !== size.x || lastSize.current.height !== size.y)) {
        fit();
        firstFit.current = true;
        lastSize.current = { width: size.x, height: size.y };
      }
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      // Remove vector paths before their renderers, including during StrictMode cleanup.
      parkFeatures.clearLayers();
      roadEdges.clearLayers();
      roadCenters.clearLayers();
      routeLayer.current?.clearLayers();
      airLayer.current?.clearLayers();
      instance.remove();
      map.current = null;
      routeLayer.current = null;
      airLayer.current = null;
      routeBounds.current = null;
      firstFit.current = false;
      lastSize.current = { width: 0, height: 0 };
    };
  }, [fit]);

  useEffect(() => {
    fit();
  }, [compact, bottomInset, fit]);

  useEffect(() => {
    const layer = routeLayer.current;
    if (!layer) return;
    layer.clearLayers();
    const compatibleRoutes = routes?.mode === mode ? routes : null;
    const coordinates = compatibleRoutes
      ? { fast: compatibleRoutes.routes.fast.polyline, clean: compatibleRoutes.routes.clean.polyline }
      : mode === 'demo' ? DEMO_ROUTES : null;
    if (!coordinates) {
      routeBounds.current = null;
      return;
    }
    const order: RouteId[] = selectedRoute === 'clean' ? ['fast', 'clean'] : ['clean', 'fast'];
    const all: L.LatLngTuple[] = [];
    for (const id of order) {
      const points = latLngs(coordinates[id]);
      if (points.length < 2) continue;
      all.push(...points);
      const active = id === selectedRoute;
      L.polyline(points, {
        pane: 'routePane', color: '#ffffff', opacity: active ? 1 : 0.75,
        weight: active ? 11 : 8, lineCap: 'round', lineJoin: 'round', interactive: false,
      }).addTo(layer);
      L.polyline(points, {
        pane: 'routePane', color: PALETTE[id], opacity: active ? 1 : 0.7,
        weight: active ? 6 : 4, lineCap: 'round', lineJoin: 'round',
        className: `route-map-route route-map-route--${active ? 'selected' : 'alternative'}`,
      }).addTo(layer).on('click', () => selection.current?.(id));
      // A generous transparent hit area makes route selection usable on a touch screen.
      L.polyline(points, { pane: 'routePane', color: '#ffffff', opacity: 0, weight: 24 }).addTo(layer)
        .on('click', () => selection.current?.(id));
    }
    const selected = latLngs(coordinates[selectedRoute]);
    if (selected.length) {
      pointMarker(selected[0], 'start').addTo(layer);
      pointMarker(selected[selected.length - 1], 'end').addTo(layer);
    }
    const previousBounds = routeBounds.current;
    routeBounds.current = all.length ? L.latLngBounds(all) : null;
    if (!previousBounds && routeBounds.current) fit();
  }, [selectedRoute, routes, mode, fit]);

  useEffect(() => {
    const layer = airLayer.current;
    if (!layer) return;
    layer.clearLayers();
    if (!pollutionVisible) return;
    if (mode === 'demo') {
      // These decorative zones are explicitly part of the sample scenario, never a live exposure model.
      const intensity = 0.48 + (Math.cos((selectedHour - 4) * Math.PI / 12) + 1) * 0.16;
      const zones: { point: L.LatLngTuple; kind: string; size: number; scale: number }[] = [
        { point: [42.69495, 23.3316], kind: 'warm', size: 245, scale: 1 },
        { point: [42.6914, 23.3325], kind: 'warm', size: 160, scale: 0.7 },
        { point: [42.6936, 23.3256], kind: 'fresh', size: 240, scale: 0.75 },
      ];
      for (const zone of zones) {
        L.marker(zone.point, {
          pane: 'airPane', interactive: false, keyboard: false,
          icon: L.divIcon({
            className: 'route-map-air-anchor',
            html: `<span class="route-map-air route-map-air--${zone.kind}" style="width:${zone.size}px;height:${zone.size}px;opacity:${intensity * zone.scale}"></span>`,
            iconSize: [0, 0],
          }),
        }).addTo(layer);
      }
    } else {
      for (const sensor of sensors) {
        const [lon, lat] = sensor.coordinates;
        if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(sensor.pm25)) continue;
        const value = Math.round(sensor.pm25);
        const marker = L.circleMarker([lat, lon], {
          radius: 7, color: '#ffffff', weight: 3, fillColor: '#537c93', fillOpacity: 0.95,
        });
        marker.bindTooltip(`PM₂.₅: ${value} µg/m³`, { direction: 'top' }).addTo(layer);
      }
    }
  }, [selectedHour, pollutionVisible, mode, sensors]);

  return <div className={`route-map ${compact ? 'route-map--compact' : 'route-map--preview'} ${className}`}>
    <div ref={host} className="route-map-canvas" aria-label="Interactive map of central Sofia with walking route alternatives" />
    <div className="route-map-controls" aria-label="Map controls">
      <button type="button" onClick={() => map.current?.zoomIn()} aria-label="Zoom in"><Plus size={19} strokeWidth={1.8} /></button>
      <button type="button" onClick={() => map.current?.zoomOut()} aria-label="Zoom out"><Minus size={19} strokeWidth={1.8} /></button>
      <button type="button" onClick={fit} aria-label="Fit routes"><LocateFixed size={19} strokeWidth={1.8} /></button>
    </div>
  </div>;
}
