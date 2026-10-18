import { describe, expect, it } from 'vitest';
import { getProgressSummary } from './progress-data';

describe('sample weekly progress', () => {
  it('builds an explicit seven-day week with five consecutive completed walking days and no invented Monday or Sunday activity', () => {
    const summary = getProgressSummary(false);
    expect(summary.days.map((day) => day.date)).toEqual(['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-17', '2026-10-18']);
    expect(summary.days.map((day) => day.walks)).toEqual([0, 1, 1, 1, 1, 1, 0]);
    expect(summary.days[0]).toMatchObject({ minutes: 0, points: 0, exposureReductionPct: null });
    expect(summary.streakDays).toBe(5);
    expect(summary.totals.walkMinutes).toBe(146);
    expect(summary.totals.walkDistanceKm).toBeCloseTo(11.5);
    expect(summary.challenge).toMatchObject({ target: 6, completed: 5, unlocked: false });
    expect(summary.recentActivity).toHaveLength(10);
    expect(summary.recentActivity[0].id).toBe('walk-sat');
  });

  it('compares complete duration-weighted exposure totals, rather than averaging route percentages or just concentrations', () => {
    const summary = getProgressSummary(false);
    // Independent arithmetic for the five same-departure route pairs in the sample ledger.
    expect(summary.totals.chosenExposure).toBeCloseTo(1599);
    expect(summary.totals.usualExposure).toBeCloseTo(2240.9);
    expect(summary.totals.exposureReductionPct).toBeCloseTo(100 * (1 - 1599 / 2240.9), 8);
    const meanDailyPercent = summary.days.filter((day) => day.completed).reduce((sum, day) => sum + day.exposureReductionPct!, 0) / 5;
    expect(summary.totals.exposureReductionPct).not.toBeCloseTo(meanDailyPercent, 3);
    expect(summary.assumptions.join(' ')).toContain('not inhaled pollution');
    expect(summary.assumptions.join(' ')).toContain('same departure time');
  });

  it('computes avoided operating emissions against each usual car journey and still counts transit emissions', () => {
    const summary = getProgressSummary(false);
    // Usual car: 30.8 km × 0.18. Chosen travel: 14.3 metro km × 0.04 + 5.6 bus km × 0.08.
    expect(summary.totals.baselineCo2Kg).toBeCloseTo(5.544, 8);
    expect(summary.totals.chosenCo2Kg).toBeCloseTo(1.02, 8);
    expect(summary.totals.avoidedCo2Kg).toBeCloseTo(4.524, 8);
    expect(summary.totals.avoidedCo2Kg).toBeLessThan(summary.totals.baselineCo2Kg);
    expect(summary.recentActivity.reduce((sum, activity) => sum + activity.avoidedCo2Kg, 0)).toBeCloseTo(4.524, 8);
    expect(summary.assumptions.join(' ')).toContain('not verified emissions factors');
  });

  it('adds Sunday exactly once, unlocks the six-walk challenge and advances the points boundary consistently', () => {
    const before = getProgressSummary(false);
    const after = getProgressSummary(true);
    expect(before).toMatchObject({ points: 300, level: 3, pointsIntoLevel: 60, pointsToNextLevel: 60 });
    expect(after).toMatchObject({ points: 360, level: 4, pointsIntoLevel: 0, pointsToNextLevel: 120, streakDays: 6, cleanerWalks: 6 });
    expect(after.challenge).toEqual({ target: 6, completed: 6, progressPct: 100, unlocked: true });
    expect(after.days[6]).toMatchObject({ completed: true, walks: 1, points: 60, minutes: 32 });
    expect(after.recentActivity[0].id).toBe('walk-sun');
    expect(after.totals.walkMinutes).toBe(178);
    expect(after.totals.chosenExposure).toBeCloseTo(1899.8);
    expect(after.totals.usualExposure).toBeCloseTo(2683.3);
    expect(after.totals.avoidedCo2Kg).toBe(before.totals.avoidedCo2Kg);
    expect(getProgressSummary(true)).toEqual(after);
    expect(new Set(after.recentActivity.map((activity) => activity.id)).size).toBe(after.recentActivity.length);
  });

  it('returns independent ledgers so resetting or editing a returned view cannot contaminate future summaries', () => {
    const original = getProgressSummary(false);
    const edited = getProgressSummary(true);
    edited.walks[0].minutes = 999;
    edited.commutes[0].distanceKm = 999;
    edited.days[0].points = 999;
    edited.assumptions.length = 0;
    expect(getProgressSummary(false)).toEqual(original);
    expect(getProgressSummary(true).points).toBe(360);
    expect(getProgressSummary(true).totals.walkMinutes).toBe(178);
  });
});
