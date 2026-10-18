import mapData from './data/sofia-map.json';
import type { Coordinate, Place, RouteId, RouteOption, RoutePlan } from './types';

/** The complete downloaded area, not the extent of ways continuing beyond its boundary. */
export const DEMO_MAP_BOUNDS = [23.313, 42.688, 23.334, 42.702] as const;

interface RoadFeature {
  properties: { kind: string; highway?: string; name?: string | null; nameEn?: string | null };
  geometry: { type: string; coordinates: unknown };
}
interface Node { coordinates: Coordinate; edges: Edge[]; name?: string }
interface Edge { a: number; b: number; metres: number; exposure: number }
interface Path { polyline: Coordinate[]; metres: number; exposureMetres: number }

const roads = (mapData.features as RoadFeature[]).filter((feature) => feature.properties.kind === 'road' && feature.geometry.type === 'LineString');
const nodes: Node[] = [];
const nodeIds = new Map<string, number>();
// Illustrative street-class factors for the offline concept only. These are not measured pollution.
export const exposureFactors: Record<string, number> = {
  primary: 2.6, secondary: 2.3, tertiary: 1.8, residential: 1.25, living_street: 1.05,
  pedestrian: 0.75, footway: 0.7, path: 0.6, steps: 0.75, service: 1.05,
};
const metresBetween = (a: Coordinate, b: Coordinate) => Math.hypot((a[0] - b[0]) * 81_700, (a[1] - b[1]) * 111_200);
const round = (value: number, digits = 1) => Math.round(value * 10 ** digits) / 10 ** digits;

function nodeId(coordinates: Coordinate): number {
  const key = coordinates.join(',');
  let id = nodeIds.get(key);
  if (id === undefined) {
    id = nodes.length;
    nodeIds.set(key, id);
    nodes.push({ coordinates, edges: [] });
  }
  return id;
}

for (const feature of roads) {
  const points = feature.geometry.coordinates as Coordinate[];
  for (let index = 1; index < points.length; index++) {
    const a = nodeId(points[index - 1]);
    const b = nodeId(points[index]);
    const name = feature.properties.nameEn || feature.properties.name;
    if (name) { nodes[a].name ??= name; nodes[b].name ??= name; }
    if (a === b) continue;
    const edge = { a, b, metres: metresBetween(points[index - 1], points[index]), exposure: exposureFactors[feature.properties.highway ?? ''] ?? 1.1 };
    nodes[a].edges.push(edge);
    nodes[b].edges.push(edge);
  }
}

// Ignore disconnected fragments at the edges of the downloaded area. We never join
// disconnected roads with invented straight-line segments or snap across the city.
const visited = new Set<number>();
let connected: number[] = [];
for (let start = 0; start < nodes.length; start++) {
  if (visited.has(start)) continue;
  const component = [start];
  visited.add(start);
  for (let index = 0; index < component.length; index++) {
    const current = component[index];
    for (const edge of nodes[current].edges) {
      const next = edge.a === current ? edge.b : edge.a;
      if (!visited.has(next)) { visited.add(next); component.push(next); }
    }
  }
  if (component.length > connected.length) connected = component;
}
const connectedIds = new Set(connected);

function nearestNode(coordinates: Coordinate): number {
  let best = connected[0];
  let bestDistance = Infinity;
  for (const id of connected) {
    const distance = metresBetween(coordinates, nodes[id].coordinates);
    if (distance < bestDistance) { best = id; bestDistance = distance; }
  }
  if (bestDistance > 180) throw new Error('Choose a nearby street or path inside the central Sofia demo map.');
  return best;
}

export function resolveDemoPlace(coordinates: Coordinate, label?: string): Place {
  const [lon, lat] = coordinates;
  const [west, south, east, north] = DEMO_MAP_BOUNDS;
  if (!Number.isFinite(lon) || !Number.isFinite(lat) || lon < west || lon > east || lat < south || lat > north) {
    throw new Error('Demo routing covers central Sofia. Choose a point inside the local map, or use Connected mode for a wider area.');
  }
  const node = nodes[nearestNode(coordinates)];
  return { label: label || node.name || 'Selected location', coordinates: [...node.coordinates] };
}

export const DEMO_TRIP: RoutePlan = {
  from: resolveDemoPlace([23.333122, 42.6915547], 'Prince’s Garden'),
  to: resolveDemoPlace([23.3149941, 42.7004183], 'Pirotska Street'),
};

const landmarks: Array<{ label: string; coordinates: Coordinate; aliases: string }> = [
  { ...DEMO_TRIP.from, aliases: "Prince's Garden Княжеска градина" },
  { ...DEMO_TRIP.to, aliases: 'Пиротска Софроний Врачански' },
  { label: 'City Garden', coordinates: [23.3249083, 42.6955847], aliases: 'Градска градина Ivan Vazov Theatre Народен театър' },
  { label: 'Central Market Hall', coordinates: [23.3212498, 42.7003665], aliases: 'Central Sofia Market Hall Централни хали' },
  { label: 'Alexander Nevsky Cathedral', coordinates: [23.3329565, 42.6962001], aliases: 'Свети Александър Невски Cathedral' },
  { label: 'Crystal Garden', coordinates: [23.3294, 42.69448], aliases: 'Кристал градина' },
  { label: 'Serdika', coordinates: [23.3224, 42.6977], aliases: 'Сердика metro station' },
  { label: 'Makedonia Square', coordinates: [23.3147158, 42.6927382], aliases: 'Македония площад' },
  { label: 'Vitosha Boulevard', coordinates: [23.3196789, 42.688924], aliases: 'Витоша булевард' },
];
const placeEntries = landmarks.map((place) => ({ place: resolveDemoPlace(place.coordinates, place.label), aliases: place.aliases }));
const groupedStreets = new Map<string, { coordinates: Coordinate[]; aliases: Set<string> }>();
for (const road of roads) {
  const label = road.properties.nameEn || road.properties.name;
  if (!label) continue;
  const street = groupedStreets.get(label) ?? { coordinates: [], aliases: new Set<string>() };
  for (const coordinate of road.geometry.coordinates as Coordinate[]) {
    const id = nodeIds.get(coordinate.join(','));
    const [west, south, east, north] = DEMO_MAP_BOUNDS;
    if (id !== undefined && connectedIds.has(id) && coordinate[0] >= west && coordinate[0] <= east && coordinate[1] >= south && coordinate[1] <= north) street.coordinates.push(coordinate);
  }
  street.aliases.add(road.properties.name ?? '');
  street.aliases.add(road.properties.nameEn ?? '');
  groupedStreets.set(label, street);
}
for (const [label, street] of groupedStreets) {
  if (street.coordinates.length === 0 || placeEntries.some((entry) => entry.place.label === label)) continue;
  const centre: Coordinate = [0, 0];
  for (const coordinate of street.coordinates) { centre[0] += coordinate[0] / street.coordinates.length; centre[1] += coordinate[1] / street.coordinates.length; }
  const coordinate = street.coordinates.reduce((best, point) => metresBetween(point, centre) < metresBetween(best, centre) ? point : best);
  placeEntries.push({ place: { label, coordinates: [...coordinate] }, aliases: [...street.aliases].join(' ') });
}
export const ROUTE_PLACES: Place[] = placeEntries.map((entry) => entry.place);
const normalized = (text: string) => text.toLocaleLowerCase().normalize('NFKD').replace(/[’'„“".,·]/g, '').trim();
export function searchRoutePlaces(query: string): Place[] {
  const words = normalized(query).split(/\s+/).filter(Boolean);
  if (!words.length) return ROUTE_PLACES.slice(0, 9);
  return placeEntries.filter((entry) => {
    const haystack = normalized(`${entry.place.label} ${entry.aliases}`);
    return words.every((word) => haystack.includes(word));
  }).map((entry) => entry.place).slice(0, 10);
}

/** A small binary heap keeps repeated route planning fast on mobile devices. */
class Queue {
  private items: Array<{ node: number; cost: number }> = [];
  push(item: { node: number; cost: number }) {
    const items = this.items;
    let index = items.length;
    items.push(item);
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (items[parent].cost <= item.cost) break;
      items[index] = items[parent]; index = parent;
    }
    items[index] = item;
  }
  pop() {
    const items = this.items;
    const first = items[0];
    const last = items.pop();
    if (items.length && last) {
      let index = 0;
      while (index * 2 + 1 < items.length) {
        let child = index * 2 + 1;
        if (child + 1 < items.length && items[child + 1].cost < items[child].cost) child++;
        if (items[child].cost >= last.cost) break;
        items[index] = items[child]; index = child;
      }
      items[index] = last;
    }
    return first;
  }
}

function shortestPath(start: number, end: number, exposureWeight: number): Path {
  const distances = new Float64Array(nodes.length).fill(Infinity);
  const previous = new Map<number, { node: number; edge: Edge }>();
  const queue = new Queue();
  distances[start] = 0;
  queue.push({ node: start, cost: 0 });
  for (let current = queue.pop(); current; current = queue.pop()) {
    if (current.cost > distances[current.node]) continue;
    if (current.node === end) break;
    for (const edge of nodes[current.node].edges) {
      const next = edge.a === current.node ? edge.b : edge.a;
      const cost = current.cost + edge.metres * (1 - exposureWeight + exposureWeight * edge.exposure);
      if (cost < distances[next]) {
        distances[next] = cost;
        previous.set(next, { node: current.node, edge });
        queue.push({ node: next, cost });
      }
    }
  }
  if (!previous.has(end)) throw new Error('No connected walking route was found. Choose different points on the local map.');
  const polyline: Coordinate[] = [[...nodes[end].coordinates]];
  let metres = 0, exposureMetres = 0, cursor = end;
  while (cursor !== start) {
    const step = previous.get(cursor)!;
    metres += step.edge.metres;
    exposureMetres += step.edge.metres * step.edge.exposure;
    cursor = step.node;
    polyline.push([...nodes[cursor].coordinates]);
  }
  return { polyline: polyline.reverse(), metres, exposureMetres };
}

const pathCache = new Map<string, Record<RouteId, Path>>();
export function getDemoRouteOptions(plan: RoutePlan, pm25: number): Record<RouteId, RouteOption> {
  const start = nearestNode(plan.from.coordinates), end = nearestNode(plan.to.coordinates);
  if (start === end || metresBetween(nodes[start].coordinates, nodes[end].coordinates) < 35) throw new Error('Choose a destination at least 35 metres from your starting point.');
  const key = `${start}:${end}`;
  let paths = pathCache.get(key);
  if (!paths) {
    paths = { fast: shortestPath(start, end, 0), balanced: shortestPath(start, end, 0.25), clean: shortestPath(start, end, 1) };
    pathCache.set(key, paths);
    if (pathCache.size > 32) pathCache.delete(pathCache.keys().next().value!);
  }
  const titles: Record<RouteId, string> = { fast: 'Fastest', balanced: 'Balanced', clean: 'Cleanest' };
  const options = Object.fromEntries((['fast', 'balanced', 'clean'] as RouteId[]).map((id) => {
    const path = paths[id];
    return [id, { id, title: titles[id], minutes: round(path.metres / 80), distanceKm: round(path.metres / 1000, 3),
      meanPm25: round(pm25 * path.exposureMetres / path.metres / 1.5), doseChangePct: 0,
      polyline: path.polyline, cleanerExists: null } satisfies RouteOption];
  })) as Record<RouteId, RouteOption>;
  const baseline = options.fast.meanPm25 * options.fast.minutes;
  for (const id of ['balanced', 'clean'] as const) {
    options[id].doseChangePct = baseline > 0 ? round(100 * (options[id].meanPm25 * options[id].minutes / baseline - 1)) : 0;
  }
  return options;
}
