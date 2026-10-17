import { useEffect, useRef, useState } from "react";

export interface PreloadStatus { loaded: number; total: number; error: string | null }
// Keep a few recent route/settings combinations across tab changes. Snapshot IDs
// isolate refreshed forecasts, and the bound prevents a growing browser cache.
const cache = new Map<string, Map<number, unknown>>();
export function retainForecast(id: string) {
  for (const key of cache.keys()) if (!key.startsWith(`${id}:`)) cache.delete(key);
}

export function useHourlyPreload<T>(key: string | null, hours: number[], selected: number,
  fetchHour: (hour: number, signal: AbortSignal) => Promise<T>, delay = 0) {
  const latest = useRef({ selected, fetchHour });
  latest.current = { selected, fetchHour };
  const [state, setState] = useState<{ key: string | null; data: Map<number, T>; status: PreloadStatus }>(
    { key: null, data: new Map(), status: { loaded: 0, total: 24, error: null } });
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    const data = (cache.get(key) ?? new Map()) as Map<number, T>;
    let failure: string | null = null;
    cache.delete(key); cache.set(key, data);
    while (cache.size > 6) cache.delete(cache.keys().next().value!);
    const publish = () => {
      if (!controller.signal.aborted) setState({ key, data, status: { loaded: data.size, total: hours.length, error: failure } });
    };
    publish();
    const timer = setTimeout(async () => {
      const queue = hours.filter((hour) => !data.has(hour));
      let failed = false;
      const worker = async () => {
        while (queue.length && !controller.signal.aborted && !failed) {
          const current = queue.indexOf(latest.current.selected);
          const hour = queue.splice(current >= 0 ? current : 0, 1)[0];
          try {
            const value = await latest.current.fetchHour(hour, controller.signal);
            if (controller.signal.aborted) return;
            data.set(hour, value); publish();
          } catch (error) {
            if (!controller.signal.aborted) { failed = true; failure = String(error); publish(); }
          }
        }
      };
      await Promise.all([worker(), worker(), worker()]);
    }, delay);
    return () => { clearTimeout(timer); controller.abort(); };
    // The key includes snapshot, travel mode and all route/overlay settings.
    // Changing just the selected hour must never restart network work.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const current = state.key === key;
  return { value: current ? state.data.get(selected) ?? null : null,
    status: current ? state.status : { loaded: 0, total: hours.length || 24, error: null } };
}
