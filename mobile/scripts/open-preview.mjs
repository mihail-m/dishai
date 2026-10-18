import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

/** Wait for the OS URL handler to acknowledge the request before the launcher exits. */
function runOpener(command, args, options, spawnProcess = spawn) {
  return new Promise((resolve, reject) => {
    let diagnostic = '';
    let settled = false;
    let child;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      error ? reject(error) : resolve();
    };
    const timer = setTimeout(() => {
      child?.kill();
      finish(new Error('The browser did not respond within 15 seconds.'));
    }, 15_000);
    try {
      child = spawnProcess(command, args, { ...options, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
      child.stderr?.on('data', chunk => { diagnostic = (diagnostic + chunk.toString()).slice(-2000); });
      child.once('error', finish);
      child.once('close', (code) => finish(code === 0 ? null : new Error(diagnostic.trim() || `Browser opener exited with code ${code}.`)));
    } catch (error) { finish(error); }
  });
}

export async function openPreview(url, { platform = process.platform, env = process.env, spawnProcess = spawn, fileExists = existsSync } = {}) {
  if (platform !== 'win32') {
    await runOpener(platform === 'darwin' ? 'open' : 'xdg-open', [url], { env }, spawnProcess);
    return;
  }
  const browserEnv = { ...env, AIRPATH_PREVIEW_URL: url };
  const prefix = ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command'];
  try {
    await runOpener('powershell.exe', [...prefix,
      "$ErrorActionPreference = 'Stop'; try { Start-Process -FilePath $env:AIRPATH_PREVIEW_URL -ErrorAction Stop; exit 0 } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }",
    ], { env: browserEnv }, spawnProcess);
  } catch (defaultError) {
    // A missing/broken Windows HTTP association should not block a local demo.
    const candidates = [
      env.ProgramFiles && join(env.ProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
      env['ProgramFiles(x86)'] && join(env['ProgramFiles(x86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      env.ProgramFiles && join(env.ProgramFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
      env.LOCALAPPDATA && join(env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ].filter(Boolean);
    const browser = candidates.find(fileExists);
    if (!browser) throw defaultError;
    await runOpener('powershell.exe', [...prefix,
      "$ErrorActionPreference = 'Stop'; try { Start-Process -FilePath $env:AIRPATH_PREVIEW_BROWSER -ArgumentList @($env:AIRPATH_PREVIEW_URL) -ErrorAction Stop; exit 0 } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }",
    ], { env: { ...browserEnv, AIRPATH_PREVIEW_BROWSER: browser } }, spawnProcess);
  }
}
