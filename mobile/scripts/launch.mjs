import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { openPreview } from "./open-preview.mjs";

const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(mobileRoot, "..");
const url = "http://127.0.0.1:5180";
const noOpen = process.argv.includes("--no-open");
process.chdir(mobileRoot);

async function showPreview() {
  if (noOpen) return;
  console.log(`Opening your browser: ${url}`);
  try {
    await openPreview(url);
    console.log("The mobile preview has been sent to your browser.");
  } catch (error) {
    throw new Error(`The preview is running, but Windows could not open a browser.\nOpen ${url} manually.\n${error.message}`);
  }
}

function findNpm() {
  const nodeDir = dirname(process.execPath);
  return [process.env.AIRPATH_NPM_CLI, process.env.npm_execpath,
    join(nodeDir, "node_modules/npm/bin/npm-cli.js"),
    resolve(nodeDir, "../lib/node_modules/npm/bin/npm-cli.js"),
  ].find((path) => path && existsSync(path));
}

async function launch() {
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < 22 || (major === 22 && minor < 12)) {
    throw new Error("AirPath Mobile needs Node.js 22.12 or newer. Install a current Node.js LTS release, then launch again.");
  }

  // Repeated clicks reuse only an identified AirPath Mobile server.
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1200) });
    const html = await response.text();
    if (html.includes('name="application-name" content="AirPath Mobile"')) {
      console.log(`AirPath Mobile is already running at ${url}`);
      // Do not treat a browser-opening failure as a failed server probe.
      return { existing: true };
    }
    throw new Error("Port 5180 is already used by another app. Close that app before launching AirPath Mobile.");
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("Port 5180")) throw error;
  }

  return { existing: false };
}

async function startPreview() {
  const { existing } = await launch();
  if (existing) { await showPreview(); return; }

  const fingerprint = createHash("sha256");
  for (const file of ["package.json", "package-lock.json", ".npmrc"]) fingerprint.update(readFileSync(join(mobileRoot, file)));
  fingerprint.update(`${process.versions.node.split(".")[0]}:${process.platform}:${process.arch}`);
  const expected = fingerprint.digest("hex");
  const stamp = join(mobileRoot, "node_modules/.airpath-dependencies");
  if (!existsSync(stamp) || readFileSync(stamp, "utf8") !== expected || !existsSync(join(mobileRoot, "node_modules/vite/package.json"))) {
    const npm = findNpm();
    if (!npm) throw new Error("npm was not found beside Node.js. Reinstall Node.js LTS with npm included, then launch again.");
    console.log("Preparing mobile dependencies. The first launch needs an internet connection...");
    await new Promise((resolveInstall, reject) => {
      const child = spawn(process.execPath, [npm, "ci", "--no-audit", "--no-fund"], {
        cwd: mobileRoot, stdio: "inherit", windowsHide: true,
        env: { ...process.env, npm_config_cache: join(repoRoot, ".local/cache/npm"), PATH: `${dirname(process.execPath)}${process.platform === "win32" ? ";" : ":"}${process.env.PATH || ""}` },
      });
      child.on("error", reject);
      child.on("exit", (code) => code === 0 ? resolveInstall() : reject(new Error(`Dependency setup failed (${code}). Check your internet connection and try again.`)));
    });
    writeFileSync(stamp, expected);
  }

  const { createServer } = await import("vite");
  const server = await createServer({ configFile: join(mobileRoot, "vite.config.ts") });
  await server.listen();
  console.log(`\nAirPath Mobile is ready: ${url}\nKeep this window open. Press Ctrl+C to stop the preview.\n`);
  const close = async () => { await server.close(); process.exit(0); };
  process.once("SIGINT", close);
  process.once("SIGTERM", close);
  try { await showPreview(); }
  catch (error) {
    // The console remains open with the useful running server and a manual URL.
    console.error(`\n${error.message}\nKeep this window open and use the URL above.\n`);
  }
}

startPreview().catch((error) => { console.error(`\nAirPath Mobile could not start: ${error.message}\n`); process.exitCode = 1; });
