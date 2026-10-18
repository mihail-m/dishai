import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => { vi.resetModules(); vi.useFakeTimers(); vi.stubEnv('VITE_GEOCODER_URL', 'https://geocoder.example/search'); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.restoreAllMocks(); });
const result = (name = 'City Garden, Sofia, Bulgaria') => ({ display_name: name, lon: '23.3248', lat: '42.6952' });

describe('optional address search', () => {
  it('makes no request when no provider is configured', async () => {
    vi.stubEnv('VITE_GEOCODER_URL', '');
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    const { searchStreetAddresses, getGeocoderUrl } = await import('./place-search');
    expect(getGeocoderUrl()).toBeNull();
    await expect(searchStreetAddresses('Sofia')).rejects.toThrow('point on the map');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('keeps only valid Sofia coordinates and caches repeat searches', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => [result(), result('Duplicate'), { ...result(), lat: '0' }, { ...result(), lon: 'invalid' }, {}] });
    vi.stubGlobal('fetch', fetcher);
    const { searchStreetAddresses } = await import('./place-search');
    const promise = searchStreetAddresses('City Garden');
    await vi.advanceTimersByTimeAsync(0);
    expect(await promise).toEqual([{ label: 'City Garden, Sofia, Bulgaria', coordinates: [23.3248, 42.6952] }]);
    await searchStreetAddresses(' city   garden ');
    expect(fetcher).toHaveBeenCalledTimes(1);
    const url = fetcher.mock.calls[0][0] as URL;
    expect(url.searchParams.get('bounded')).toBe('1');
    expect(url.searchParams.get('countrycodes')).toBe('bg');
  });

  it('spaces distinct requests and never sends a queued request cancelled when the dialog closes', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => [result()] });
    vi.stubGlobal('fetch', fetcher);
    const { searchStreetAddresses } = await import('./place-search');
    const first = searchStreetAddresses('First address');
    await vi.advanceTimersByTimeAsync(0); await first;
    const controller = new AbortController();
    const cancelled = searchStreetAddresses('Cancelled address', controller.signal);
    const cancellation = expect(cancelled).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0); controller.abort(); await cancellation;
    const second = searchStreetAddresses('Second address');
    await vi.advanceTimersByTimeAsync(1099); expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); await second;
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('reports provider failures without poisoning the next search', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ ok: false }).mockResolvedValueOnce({ ok: true, json: async () => [result()] });
    vi.stubGlobal('fetch', fetcher);
    const { searchStreetAddresses } = await import('./place-search');
    const failed = expect(searchStreetAddresses('First address')).rejects.toThrow('unavailable');
    await vi.advanceTimersByTimeAsync(0); await failed;
    const retry = searchStreetAddresses('First address');
    await vi.advanceTimersByTimeAsync(1100);
    expect(await retry).toHaveLength(1);
  });
});
