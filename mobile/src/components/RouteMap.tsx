import { useCallback, useEffect, useRef, type CSSProperties } from 'react';
import L from 'leaflet';
import { LocateFixed, Minus, Plus } from 'lucide-react';
import 'leaflet/dist/leaflet.css';
import '../map.css';
import sofia from '../data/sofia-map.json';
import type { StreetAirData } from '../map-air';
import type { DataMode, RouteData, RouteId, Sensor } from '../types';

type Coordinate = [number, number];

export interface RouteMapProps {
  selectedRoute: RouteId;
  routes?: RouteData | null;
  selectedHour?: number;
  streetsVisible?: boolean;
  sensorsVisible?: boolean;
  streetAir?: StreetAirData | null;
  mode?: DataMode;
  sensors?: Sensor[];
  /** True while the route comparison sheet is expanded over the map. */
  compact?: boolean;
  /** Pixels covered by the bottom sheet and navigation; defaults follow the demo layout. */
  bottomInset?: number;
  /** Space covered by the departure wheel or other controls above the map. */
  topInset?: number;
  /** The next map tap sets this route endpoint. */
  picking?: 'from' | 'to' | null;
  onPickPlace?: (coordinates: Coordinate) => void;
  onSelectRoute?: (route: RouteId) => void;
  className?: string;
}

const PALETTE = { clean: '#6564ed', balanced: '#3fa99b', fast: '#e99a66' };
const ROUTE_IDS: RouteId[] = ['fast', 'balanced', 'clean'];

const LABELS = [
  { point: [42.69482, 23.32535], text: 'CITY GARDEN', kind: 'park' },
  { point: [42.6905, 23.3341], text: 'PRINCE’S GARDEN', kind: 'park' },
  { point: [42.69448, 23.3294], text: 'CRYSTAL GARDEN', kind: 'park' },
  { point: [42.69608, 23.33295], text: 'ALEXANDER NEVSKY', kind: 'landmark' },
  { point: [42.69655, 23.3224], text: 'SOFIA CENTRE', kind: 'district' },
  { point: [42.69973, 23.3213], text: 'CENTRAL MARKET', kind: 'landmark' },
  { point: [42.70045, 23.3167], text: 'Pirotska St', kind: 'street', angle: -12 },
  { point: [42.6978, 23.3216], text: 'SERDIKA', kind: 'district' },
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

// A continuous concentration palette, not an official air-quality index.
function concentrationColor(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0) return '#a5b3b7';
  const stops: [number, [number, number, number]][] = [
    [0, [65, 171, 151]], [15, [169, 188, 113]], [25, [229, 178, 103]], [40, [221, 123, 109]], [70, [170, 95, 113]],
  ];
  let index = 0;
  while (index < stops.length - 2 && value > stops[index + 1][0]) index++;
  const [lo, a] = stops[index], [hi, b] = stops[index + 1];
  const t = Math.max(0, Math.min(1, (value - lo) / (hi - lo)));
  return `rgb(${a.map((channel, i) => Math.round(channel + (b[i] - channel) * t)).join(',')})`;
}

function streetScoreColor(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0 || value > 100) return '#a5b3b7';
  const stops: [number, [number, number, number]][] = [
    [0, [212, 113, 109]], [35, [228, 160, 111]], [60, [223, 194, 121]], [80, [153, 193, 143]], [100, [65, 171, 151]],
  ];
  let index = 0;
  while (index < stops.length - 2 && value > stops[index + 1][0]) index++;
  const [lo, a] = stops[index], [hi, b] = stops[index + 1];
  const t = Math.max(0, Math.min(1, (value - lo) / (hi - lo)));
  return `rgb(${a.map((channel, i) => Math.round(channel + (b[i] - channel) * t)).join(',')})`;
}

function airDetail(title: string, value: string, detail: string): HTMLElement {
  const content = document.createElement('div');
  content.className = 'map-air-detail';
  for (const [tag, text] of [['strong', title], ['span', value], ['small', detail]]) {
    const line = document.createElement(tag);
    line.textContent = text;
    content.append(line);
  }
  return content;
}

function sensorReadingTime(sensor: Sensor): string {
  return sensor.timestamp && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(sensor.timestamp)
    ? `Reading ${sensor.timestamp.slice(0, 10)} · ${sensor.timestamp.slice(11, 16)}`
    : 'Reading time unavailable';
}

function bindAirPopup(layer: L.Path, content: HTMLElement, pickPoint: (event: L.LeafletMouseEvent) => boolean) {
  layer.bindPopup(content, { className: 'map-air-popup', maxWidth: 240 });
  // Leaflet's default popup handler stops the map click before address picking.
  // Route the click explicitly so picking never opens or auto-pans a popup.
  layer.off('click');
  layer.on('click', (event: L.LeafletMouseEvent) => {
    if (pickPoint(event)) { layer.closePopup(); return; }
    layer.openPopup(event.latlng);
  });
}

/** Local OSM streets keep demo mode reliable; connected mode adds the wider OSM basemap. */
export default function RouteMap({
  selectedRoute,
  routes,
  selectedHour = 0,
  streetsVisible = false,
  sensorsVisible = false,
  streetAir,
  mode = 'demo',
  sensors = [],
  compact = true,
  bottomInset,
  topInset = 110,
  picking = null,
  onPickPlace,
  onSelectRoute,
  className = '',
}: RouteMapProps) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const routeLayer = useRef<L.LayerGroup | null>(null);
  const streetAirLayer = useRef<L.LayerGroup | null>(null);
  const sensorLayer = useRef<L.LayerGroup | null>(null);
  const paintStreetAir = useRef<(() => void) | null>(null);
  const streetHour = useRef(selectedHour);
  const localMapLayer = useRef<L.LayerGroup | null>(null);
  const onlineMapLayer = useRef<L.TileLayer | null>(null);
  const routeBounds = useRef<L.LatLngBounds | null>(null);
  const selection = useRef(onSelectRoute);
  const placeSelection = useRef({ picking, onPickPlace });
  const fittedEndpoints = useRef<string | null>(null);
  const firstFit = useRef(false);
  const lastSize = useRef({ width: 0, height: 0 });
  const layout = useRef({ compact, bottomInset, topInset });
  selection.current = onSelectRoute;
  placeSelection.current = { picking, onPickPlace };
  layout.current = { compact, bottomInset, topInset };
  streetHour.current = selectedHour;

  const pickAirPoint = useCallback((event: L.LeafletMouseEvent) => {
    if (!placeSelection.current.picking) return false;
    placeSelection.current.onPickPlace?.([event.latlng.lng, event.latlng.lat]);
    return true;
  }, []);

  const fit = useCallback(() => {
    const instance = map.current;
    if (!instance) return;
    const bounds = routeBounds.current;
    if (bounds?.isValid()) {
      const height = instance.getSize().y;
      const requested = layout.current.bottomInset ?? (layout.current.compact ? 480 : 250);
      // Respect the measured sheet edge even on shorter screens. Reserving a
      // fixed large viewport here would place the lower endpoint behind it.
      const bottom = Math.min(requested, Math.max(0, height - 96));
      const top = Math.min(layout.current.topInset, Math.max(16, height - bottom - 80));
      instance.fitBounds(bounds, {
        paddingTopLeft: [28, top],
        paddingBottomRight: [40, bottom],
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
      minZoom: 10,
      maxZoom: 18.5,
      zoomControl: false,
      attributionControl: true,
      scrollWheelZoom: false,
    });
    map.current = instance;
    instance.attributionControl.setPrefix(false);
    instance.attributionControl.setPosition('bottomright');
    instance.attributionControl.addAttribution('<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>');
    instance.on('click', (event: L.LeafletMouseEvent) => {
      if (placeSelection.current.picking) {
        placeSelection.current.onPickPlace?.([event.latlng.lng, event.latlng.lat]);
      }
    });

    const localMap = L.layerGroup().addTo(instance);
    localMapLayer.current = localMap;
    onlineMapLayer.current = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      crossOrigin: true,
    });

    instance.createPane('parkPane').style.zIndex = '200';
    instance.createPane('roadPane').style.zIndex = '220';
    instance.createPane('streetAirPane').style.zIndex = '350';
    instance.createPane('routePane').style.zIndex = '430';
    instance.createPane('sensorPane').style.zIndex = '460';
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
    }).addTo(localMap);
    const roadEdges = L.geoJSON(roads as Parameters<typeof L.geoJSON>[0], {
      interactive: false,
      style: (feature) => {
        const main = ['primary', 'secondary', 'tertiary'].includes(feature?.properties.highway);
        const path = ['footway', 'path', 'steps'].includes(feature?.properties.highway);
        return { renderer: roadRenderer, color: path ? '#d0dcd9' : '#d3dfe0', weight: main ? 12 : path ? 3 : 7, opacity: path ? 0.7 : 0.95, lineCap: 'round' };
      },
    }).addTo(localMap);
    const roadCenters = L.geoJSON(roads as Parameters<typeof L.geoJSON>[0], {
      interactive: false,
      style: (feature) => {
        const main = ['primary', 'secondary', 'tertiary'].includes(feature?.properties.highway);
        const path = ['footway', 'path', 'steps'].includes(feature?.properties.highway);
        return { renderer: roadRenderer, color: path ? '#f1f6ef' : '#ffffff', weight: main ? 9 : path ? 1.5 : 4.5, opacity: 0.96, lineCap: 'round' };
      },
    }).addTo(localMap);

    for (const label of LABELS) {
      L.marker(label.point as L.LatLngTuple, {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: 'route-map-label-anchor',
          html: `<span class="route-map-label route-map-label--${label.kind}" style="--label-angle:${label.angle ?? 0}deg">${label.text}</span>`,
          iconSize: [0, 0],
        }),
      }).addTo(localMap);
    }

    routeLayer.current = L.layerGroup().addTo(instance);
    streetAirLayer.current = L.layerGroup().addTo(instance);
    sensorLayer.current = L.layerGroup().addTo(instance);
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
      streetAirLayer.current?.clearLayers();
      sensorLayer.current?.clearLayers();
      instance.remove();
      map.current = null;
      routeLayer.current = null;
      streetAirLayer.current = null;
      sensorLayer.current = null;
      paintStreetAir.current = null;
      localMapLayer.current = null;
      onlineMapLayer.current = null;
      routeBounds.current = null;
      firstFit.current = false;
      fittedEndpoints.current = null;
      lastSize.current = { width: 0, height: 0 };
    };
  }, [fit]);

  useEffect(() => {
    const instance = map.current;
    const localMap = localMapLayer.current;
    const onlineMap = onlineMapLayer.current;
    if (!instance || !localMap || !onlineMap) return;
    if (mode === 'live') {
      instance.removeLayer(localMap);
      onlineMap.addTo(instance);
    } else {
      instance.removeLayer(onlineMap);
      localMap.addTo(instance);
    }
  }, [mode]);

  useEffect(() => {
    fit();
  }, [compact, bottomInset, topInset, fit]);

  useEffect(() => {
    const layer = routeLayer.current;
    if (!layer) return;
    layer.clearLayers();
    const compatibleRoutes = routes?.mode === mode ? routes : null;
    if (!compatibleRoutes) {
      routeBounds.current = null;
      fittedEndpoints.current = null;
      return;
    }
    const coordinates = Object.fromEntries(ROUTE_IDS.map((id) => [id, compatibleRoutes.routes[id].polyline])) as Record<RouteId, Coordinate[]>;
    // Draw the selected alternative last so shared streets remain easy to follow.
    const order = [...ROUTE_IDS.filter((id) => id !== selectedRoute), selectedRoute];
    const all: L.LatLngTuple[] = [];
    for (const id of order) {
      const points = latLngs(coordinates[id]);
      if (points.length < 2) continue;
      all.push(...points);
      const active = id === selectedRoute;
      const onRouteClick = (event: L.LeafletMouseEvent) => {
        if (placeSelection.current.picking) {
          placeSelection.current.onPickPlace?.([event.latlng.lng, event.latlng.lat]);
        } else {
          selection.current?.(id);
        }
      };
      L.polyline(points, {
        pane: 'routePane', color: '#ffffff', opacity: active ? 1 : 0.75,
        weight: active ? 11 : 9, lineCap: 'round', lineJoin: 'round', interactive: false,
      }).addTo(layer);
      L.polyline(points, {
        pane: 'routePane', color: PALETTE[id], opacity: active ? 1 : 0.85,
        weight: active ? 6 : 4.5, lineCap: 'round', lineJoin: 'round', bubblingMouseEvents: false,
        className: `route-map-route route-map-route--${active ? 'selected' : 'alternative'}`,
      }).addTo(layer).on('click', onRouteClick);
      // A generous transparent hit area makes route selection usable on a touch screen.
      L.polyline(points, { pane: 'routePane', color: '#ffffff', opacity: 0, weight: 24, bubblingMouseEvents: false }).addTo(layer)
        .on('click', onRouteClick);
    }
    const selected = latLngs(coordinates[selectedRoute]);
    if (selected.length) {
      pointMarker(selected[0], 'start').addTo(layer);
      pointMarker(selected[selected.length - 1], 'end').addTo(layer);
    }
    routeBounds.current = all.length ? L.latLngBounds(all) : null;
    const endpointKey = `${mode}:${compatibleRoutes.from.coordinates.join(',')}:${compatibleRoutes.to.coordinates.join(',')}`;
    if (routeBounds.current && fittedEndpoints.current !== endpointKey) {
      fittedEndpoints.current = endpointKey;
      fit();
    }
  }, [selectedRoute, routes, mode, fit]);

  useEffect(() => {
    const instance = map.current;
    const layer = streetAirLayer.current;
    if (!instance || !layer) return;
    layer.clearLayers();
    paintStreetAir.current = null;
    if (!streetsVisible || !streetAir || streetAir.mode !== mode) return;

    const segments = streetAir.segments.map((segment, index) => {
      const points = latLngs(segment.coordinates);
      return { segment, index, points, bounds: points.length >= 2 ? L.latLngBounds(points) : null };
    });
    const paths = new Map<number, { line: L.Polyline; score: number | null; time: string }>();
    const paint = () => {
      const current = streetAir.hours[streetHour.current];
      const viewport = instance.getBounds().pad(0.1);
      const zoom = instance.getZoom();
      const visible = new Set<number>();
      for (const { segment, index, points, bounds } of segments) {
        // Match normal basemap detail: close views show every street, city-wide
        // views retain the main network without creating thousands of tiny SVGs.
        if (!bounds || !viewport.intersects(bounds)
          || (zoom < 13 && segment.roadClass !== 'major')
          || (zoom < 14.5 && !['major', 'collector'].includes(segment.roadClass))) continue;
        visible.add(index);
        const rawScore = current?.scores[index];
        const score = typeof rawScore === 'number' && Number.isFinite(rawScore) && rawScore >= 0 && rawScore <= 100 ? rawScore : null;
        const time = current?.time ?? '';
        let entry = paths.get(index);
        if (!entry) {
          const line = L.polyline(points, {
            pane: 'streetAirPane', weight: { major: 5, collector: 4, local: 3, path: 2.5 }[segment.roadClass],
            color: streetScoreColor(score), opacity: 0.86, lineCap: 'round', lineJoin: 'round',
            className: 'street-air-segment', bubblingMouseEvents: false,
          }).addTo(layer);
          entry = { line, score: null, time: '' };
          paths.set(index, entry);
          const element = line.getElement();
          element?.setAttribute('data-street-id', String(segment.id));
        }
        if (entry.score !== score || entry.time !== time || !entry.line.getTooltip()) {
          const title = segment.name || 'Street segment';
          const value = score === null ? 'Street score unavailable' : `AirPath street score ${Math.round(score)}/100`;
          const stamp = time ? `${time.slice(0, 10)} · ${time.slice(11, 16)}` : 'Forecast time unavailable';
          const detail = `${mode === 'demo' ? 'Demo estimate' : 'Model estimate'} · ${stamp}`;
          entry.line.setStyle({ color: streetScoreColor(score) });
          if (entry.line.getTooltip()) {
            entry.line.setTooltipContent(airDetail(title, value, detail));
            entry.line.setPopupContent(airDetail(title, value, detail));
          } else {
            entry.line.bindTooltip(airDetail(title, value, detail), { direction: 'top', sticky: true, className: 'map-air-tooltip' });
            bindAirPopup(entry.line, airDetail(title, value, detail), pickAirPoint);
          }
          const element = entry.line.getElement();
          element?.setAttribute('data-street-score', score === null ? 'unavailable' : String(score));
          element?.setAttribute('data-forecast-time', time);
          element?.setAttribute('aria-label', `${title}. ${value}. ${detail}`);
          entry.score = score;
          entry.time = time;
        }
      }
      for (const [index, { line }] of paths) {
        if (!visible.has(index)) { layer.removeLayer(line); paths.delete(index); }
      }
    };
    paintStreetAir.current = paint;
    instance.on('moveend', paint);
    paint();
    return () => {
      instance.off('moveend', paint);
      paintStreetAir.current = null;
      layer.clearLayers();
      paths.clear();
    };
  }, [streetsVisible, streetAir, mode, pickAirPoint]);

  useEffect(() => {
    paintStreetAir.current?.();
  }, [selectedHour]);

  useEffect(() => {
    const layer = sensorLayer.current;
    if (!layer) return;
    layer.clearLayers();
    if (!sensorsVisible) return;
    for (const sensor of sensors) {
      const [lon, lat] = sensor.coordinates;
      if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180
        || !Number.isFinite(sensor.pm25) || sensor.pm25 < 0) continue;
      const value = `PM₂.₅ ${sensor.pm25.toFixed(1)} µg/m³`;
      const detail = `${mode === 'demo' ? 'Sample sensor · ' : 'Latest available · '}${sensorReadingTime(sensor)}`;
      const marker = L.circleMarker([lat, lon], {
        pane: 'sensorPane', radius: 7.5, color: '#ffffff', weight: 2.5,
        fillColor: concentrationColor(sensor.pm25), fillOpacity: 1,
        className: 'air-sensor-marker', bubblingMouseEvents: false,
      }).addTo(layer);
      marker.bindTooltip(airDetail(sensor.name, value, detail), { direction: 'top', className: 'map-air-tooltip' });
      bindAirPopup(marker, airDetail(sensor.name, value, detail), pickAirPoint);
      const element = marker.getElement();
      element?.setAttribute('data-sensor-id', sensor.id);
      element?.setAttribute('data-pm25', String(sensor.pm25));
      element?.setAttribute('aria-label', `${sensor.name}. ${value}. ${detail}`);
    }
    return () => { layer.clearLayers(); };
  }, [sensorsVisible, mode, sensors, pickAirPoint]);

  return <div className={`route-map ${compact ? 'route-map--compact' : 'route-map--preview'} ${picking ? 'route-map--picking' : ''} ${className}`}
    style={{ '--map-bottom-inset': `${bottomInset ?? (compact ? 480 : 250)}px` } as CSSProperties}>
    <div ref={host} className="route-map-canvas" aria-label={picking ? `Choose your ${picking === 'from' ? 'starting point' : 'destination'} on the map` : 'Interactive map of Sofia with three walking route alternatives'} />
    <div className="route-map-controls" aria-label="Map controls">
      <button type="button" onClick={() => map.current?.zoomIn()} aria-label="Zoom in"><Plus size={19} strokeWidth={1.8} /></button>
      <button type="button" onClick={() => map.current?.zoomOut()} aria-label="Zoom out"><Minus size={19} strokeWidth={1.8} /></button>
      <button type="button" onClick={fit} aria-label="Fit routes"><LocateFixed size={19} strokeWidth={1.8} /></button>
    </div>
  </div>;
}
