/** Repeatable sample activity for the Progress concept. Never derived from live location or sensor readings. */
export interface CleanerWalk {
  id: string;
  date: string;
  time: string;
  title: string;
  distanceKm: number;
  minutes: number;
  chosenPm25: number;
  usualMinutes: number;
  usualPm25: number;
}

export type CommuteMode = 'car' | 'bus' | 'metro' | 'bike' | 'walk';

export interface SampleCommute {
  id: string;
  date: string;
  time: string;
  title: string;
  mode: Exclude<CommuteMode, 'car'>;
  distanceKm: number;
  minutes: number;
  usualCarDistanceKm: number;
}

export interface ProgressDay {
  date: string;
  label: string;
  completed: boolean;
  walks: number;
  minutes: number;
  distanceKm: number;
  points: number;
  exposureReductionPct: number | null;
}

export interface ProgressActivity {
  id: string;
  date: string;
  time: string;
  title: string;
  subtitle: string;
  kind: 'cleaner-walk' | 'commute';
  points: number;
  minutes: number;
  distanceKm: number;
  exposureReductionPct: number | null;
  avoidedCo2Kg: number;
}

export interface ProgressSummary {
  weekLabel: string;
  weekStart: string;
  weekEnd: string;
  points: number;
  level: number;
  pointsIntoLevel: number;
  pointsToNextLevel: number;
  streakDays: number;
  cleanerWalks: number;
  challenge: { target: number; completed: number; progressPct: number; unlocked: boolean };
  days: ProgressDay[];
  recentActivity: ProgressActivity[];
  walks: CleanerWalk[];
  commutes: SampleCommute[];
  totals: {
    walkMinutes: number;
    walkDistanceKm: number;
    /** Sum of route-average PM2.5 × walking minutes (µg·min/m³), not inhaled dose. */
    usualExposure: number;
    chosenExposure: number;
    exposureReductionPct: number;
    baselineCo2Kg: number;
    chosenCo2Kg: number;
    avoidedCo2Kg: number;
  };
  assumptions: string[];
}

export const POINTS_PER_WALK = 60;
export const POINTS_PER_LEVEL = 120;
export const WEEKLY_WALK_TARGET = 6;

/** Illustrative operating emissions per passenger kilometre, not audited vehicle or transit factors. */
export const DEMO_CO2_KG_PER_KM: Readonly<Record<CommuteMode, number>> = Object.freeze({
  car: 0.18, bus: 0.08, metro: 0.04, bike: 0, walk: 0,
});

export const PROGRESS_ASSUMPTIONS: readonly string[] = Object.freeze([
  'Sample week: 12–18 October 2026. All activities, route concentrations and transport factors are illustrative, including in Connected mode.',
  'Route exposure compares the sum of route-average PM2.5 × walking minutes for the chosen and usual walking routes at the same departure time. The reduction is 1 − chosen total / usual total; it is not inhaled pollution or a measured health benefit.',
  'Transport CO₂ compares each sample commute with its usual car journey. Assumed operating factors per passenger kilometre: car 0.18 kg, bus 0.08 kg, metro 0.04 kg, cycling and walking 0 kg. These are demo assumptions, not verified emissions factors; manufacturing and food production are excluded.',
  'The Sunday demo walk changes walking progress only. It does not add a car journey or claim additional avoided transport emissions.',
  'Each completed cleaner walk earns 60 points. Levels advance every 120 points; the weekly challenge is six walks. The streak counts consecutive activity days through the latest completed day in this sample week.',
]);

const baselineWalks: readonly CleanerWalk[] = [
  { id: 'walk-tue', date: '2026-10-13', time: '08:30', title: 'A quieter way to work', distanceKm: 1.9, minutes: 24, chosenPm25: 12.6, usualMinutes: 21, usualPm25: 19.4 },
  { id: 'walk-wed', date: '2026-10-14', time: '12:20', title: 'A break in City Garden', distanceKm: 2.2, minutes: 28, chosenPm25: 10.8, usualMinutes: 24, usualPm25: 17.5 },
  { id: 'walk-thu', date: '2026-10-15', time: '17:40', title: 'The greener way home', distanceKm: 2, minutes: 26, chosenPm25: 14.1, usualMinutes: 23, usualPm25: 22.8 },
  { id: 'walk-fri', date: '2026-10-16', time: '13:10', title: 'An afternoon outdoors', distanceKm: 2.4, minutes: 30, chosenPm25: 9.9, usualMinutes: 26, usualPm25: 16.3 },
  { id: 'walk-sat', date: '2026-10-17', time: '10:30', title: 'A slower Saturday morning', distanceKm: 3, minutes: 38, chosenPm25: 8.7, usualMinutes: 33, usualPm25: 14.1 },
];

const sundayWalk: CleanerWalk = {
  id: 'walk-sun', date: '2026-10-18', time: '14:00', title: 'Sunday through the gardens',
  distanceKm: 2.5, minutes: 32, chosenPm25: 9.4, usualMinutes: 28, usualPm25: 15.8,
};

const sampleCommutes: readonly SampleCommute[] = [
  { id: 'commute-tue', date: '2026-10-13', time: '18:00', title: 'Home by bike', mode: 'bike', distanceKm: 3.8, minutes: 19, usualCarDistanceKm: 4.2 },
  { id: 'commute-wed', date: '2026-10-14', time: '08:00', title: 'The morning metro', mode: 'metro', distanceKm: 7.4, minutes: 24, usualCarDistanceKm: 8.1 },
  { id: 'commute-thu', date: '2026-10-15', time: '08:15', title: 'Into the centre by bus', mode: 'bus', distanceKm: 5.6, minutes: 28, usualCarDistanceKm: 6 },
  { id: 'commute-fri', date: '2026-10-16', time: '18:10', title: 'An evening cycle', mode: 'bike', distanceKm: 4.5, minutes: 23, usualCarDistanceKm: 4.9 },
  { id: 'commute-sat', date: '2026-10-17', time: '09:30', title: 'A weekend metro trip', mode: 'metro', distanceKm: 6.9, minutes: 22, usualCarDistanceKm: 7.6 },
];

const total = <T>(items: readonly T[], value: (item: T) => number) => items.reduce((sum, item) => sum + value(item), 0);
const exposure = (walks: readonly CleanerWalk[]) => {
  const chosen = total(walks, (walk) => walk.chosenPm25 * walk.minutes);
  const usual = total(walks, (walk) => walk.usualPm25 * walk.usualMinutes);
  return { chosen, usual, reductionPct: usual > 0 ? 100 * (1 - chosen / usual) : null };
};
const commuteEmissions = (commute: SampleCommute) => ({
  baseline: commute.usualCarDistanceKm * DEMO_CO2_KG_PER_KM.car,
  chosen: commute.distanceKm * DEMO_CO2_KG_PER_KM[commute.mode],
});

/** A boolean selects one extra ledger entry; calling repeatedly can never duplicate activity or points. */
export function getProgressSummary(completedDemoWalk: boolean): ProgressSummary {
  const walks = [...baselineWalks, ...(completedDemoWalk ? [sundayWalk] : [])].map((walk) => ({ ...walk }));
  const commutes = sampleCommutes.map((commute) => ({ ...commute }));
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((label, index): ProgressDay => {
    const date = `2026-10-${12 + index}`;
    const dailyWalks = walks.filter((walk) => walk.date === date);
    return { date, label, completed: dailyWalks.length > 0, walks: dailyWalks.length,
      minutes: total(dailyWalks, (walk) => walk.minutes), distanceKm: total(dailyWalks, (walk) => walk.distanceKm),
      points: dailyWalks.length * POINTS_PER_WALK, exposureReductionPct: exposure(dailyWalks).reductionPct };
  });
  let latestActivityDay = days.length - 1;
  while (latestActivityDay >= 0 && !days[latestActivityDay].completed) latestActivityDay--;
  let streakDays = 0;
  while (latestActivityDay >= 0 && days[latestActivityDay].completed) { streakDays++; latestActivityDay--; }
  const points = walks.length * POINTS_PER_WALK;
  const air = exposure(walks);
  const emissions = commutes.map(commuteEmissions);
  const baselineCo2Kg = total(emissions, (entry) => entry.baseline);
  const chosenCo2Kg = total(emissions, (entry) => entry.chosen);
  const walkActivity = walks.map((walk): ProgressActivity => ({ id: walk.id, date: walk.date, time: walk.time, title: walk.title,
    subtitle: 'Sample cleaner walk', kind: 'cleaner-walk', points: POINTS_PER_WALK, minutes: walk.minutes, distanceKm: walk.distanceKm,
    exposureReductionPct: exposure([walk]).reductionPct, avoidedCo2Kg: 0 }));
  const commuteActivity = commutes.map((commute): ProgressActivity => {
    const carbon = commuteEmissions(commute);
    return { id: commute.id, date: commute.date, time: commute.time, title: commute.title, subtitle: `Sample ${commute.mode} commute`,
      kind: 'commute', points: 0, minutes: commute.minutes, distanceKm: commute.distanceKm,
      exposureReductionPct: null, avoidedCo2Kg: carbon.baseline - carbon.chosen };
  });
  return {
    weekLabel: '12–18 October', weekStart: '2026-10-12', weekEnd: '2026-10-18', points,
    level: Math.floor(points / POINTS_PER_LEVEL) + 1,
    pointsIntoLevel: points % POINTS_PER_LEVEL, pointsToNextLevel: POINTS_PER_LEVEL - points % POINTS_PER_LEVEL,
    streakDays, cleanerWalks: walks.length,
    challenge: { target: WEEKLY_WALK_TARGET, completed: walks.length,
      progressPct: Math.min(100, 100 * walks.length / WEEKLY_WALK_TARGET), unlocked: walks.length >= WEEKLY_WALK_TARGET },
    days, recentActivity: [...walkActivity, ...commuteActivity].sort((a, b) => `${b.date}T${b.time}`.localeCompare(`${a.date}T${a.time}`)),
    walks, commutes,
    totals: { walkMinutes: total(walks, (walk) => walk.minutes), walkDistanceKm: total(walks, (walk) => walk.distanceKm),
      usualExposure: air.usual, chosenExposure: air.chosen, exposureReductionPct: air.reductionPct ?? 0,
      baselineCo2Kg, chosenCo2Kg, avoidedCo2Kg: baselineCo2Kg - chosenCo2Kg },
    assumptions: [...PROGRESS_ASSUMPTIONS],
  };
}
