import { EventEmitter } from 'node:events';
import { describe, it, expect, vi } from 'vitest';
import { openPreview } from './open-preview.mjs';

function opener() { const child = new EventEmitter(); child.stderr = new EventEmitter(); child.kill = vi.fn(); return child; }

describe('opening the preview', () => {
  it('keeps the launcher alive until Windows finishes opening the browser', async () => {
    const child = opener();
    const spawnProcess = vi.fn(() => child);
    let finished = false;
    const promise = openPreview('http://127.0.0.1:5180', { platform: 'win32', env: {}, spawnProcess }).then(() => { finished = true; });
    await Promise.resolve();
    expect(finished).toBe(false);
    expect(spawnProcess.mock.calls[0][2]).not.toHaveProperty('detached');
    child.emit('close', 0);
    await promise;
    expect(finished).toBe(true);
  });

  it('falls back to an installed browser if the Windows URL association fails', async () => {
    const first = opener(), second = opener();
    const spawnProcess = vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second);
    const promise = openPreview('http://127.0.0.1:5180', { platform: 'win32', env: { ProgramFiles: 'C:/Program Files' }, spawnProcess, fileExists: () => true });
    first.stderr.emit('data', 'No application is associated with HTTP.');
    first.emit('close', 1);
    await Promise.resolve();
    expect(spawnProcess).toHaveBeenCalledTimes(2);
    expect(spawnProcess.mock.calls[1][2].env.AIRPATH_PREVIEW_BROWSER).toContain('chrome.exe');
    second.emit('close', 0);
    await promise;
  });

  it('reports browser failures instead of returning success', async () => {
    const child = opener();
    const promise = openPreview('http://127.0.0.1:5180', { platform: 'win32', env: {}, spawnProcess: () => child, fileExists: () => false });
    child.stderr.emit('data', 'Browser launch denied.');
    child.emit('close', 1);
    await expect(promise).rejects.toThrow('Browser launch denied.');
  });
});
