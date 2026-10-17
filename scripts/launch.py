"""Shared, project-local setup and server lifecycle for the double-click launchers."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener, urlopen
import webbrowser
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
FRONTEND = ROOT / "frontend"
STATE = LOCAL / "server.json"
NODE_VERSION = "22.23.3"


def run(*command: str | Path, cwd: Path = ROOT) -> None:
    subprocess.run([str(c) for c in command], cwd=cwd, check=True)


def fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()


def current_stamp(path: Path, value: str) -> bool:
    return path.is_file() and path.read_text(encoding="utf-8") == value


def ensure_python(uv: Path) -> Path:
    venv = LOCAL / "venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("Creating the app's Python environment...", flush=True)
        run(uv, "venv", "--no-config", "--python", sys.executable, venv)
    requirements = ROOT / "requirements-prod.txt"
    stamp = venv / ".dishai-requirements"
    key = fingerprint([requirements])
    if not current_stamp(stamp, key):
        print("Installing backend dependencies (first launch may take a few minutes)...", flush=True)
        run(uv, "pip", "install", "--no-config", "--python", python, "-r", requirements)
        stamp.write_text(key, encoding="utf-8")
    return python


def node_distribution(system: str, machine: str) -> tuple[str, str]:
    if system == "Windows":
        return "win-x64", "zip"
    if system == "Darwin" and machine in ("arm64", "aarch64", "x86_64"):
        return "darwin-" + ("arm64" if machine != "x86_64" else "x64"), "tar.gz"
    raise RuntimeError("The clickable launchers support 64-bit Windows and macOS.")


def ensure_node() -> tuple[Path, Path]:
    target, extension = node_distribution(platform.system(), platform.machine())
    name = f"node-v{NODE_VERSION}-{target}"
    folder = LOCAL / "node" / name
    node = folder / ("node.exe" if os.name == "nt" else "bin/node")
    npm = folder / ("node_modules/npm/bin/npm-cli.js" if os.name == "nt" else "lib/node_modules/npm/bin/npm-cli.js")
    marker = folder / ".dishai-complete"
    if not marker.exists():
        print(f"Downloading Node.js {NODE_VERSION}...", flush=True)
        filename = f"{name}.{extension}"
        base = f"https://nodejs.org/dist/v{NODE_VERSION}"
        folder.parent.mkdir(parents=True, exist_ok=True)
        archive = folder.parent / filename
        with urlopen(f"{base}/SHASUMS256.txt", timeout=60) as response:
            checksums = dict(line.split()[::-1] for line in response.read().decode().splitlines() if line.strip())
        with urlopen(f"{base}/{filename}", timeout=120) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != checksums.get(filename):
            archive.unlink()
            raise RuntimeError("Node.js download failed its checksum check. Please launch again.")
        if extension == "zip":
            with zipfile.ZipFile(archive) as package:
                package.extractall(folder.parent)
        else:
            with tarfile.open(archive) as package:
                package.extractall(folder.parent, filter="data")
        if not node.is_file() or not npm.is_file():
            raise RuntimeError("Node.js extraction was incomplete. Please launch again.")
        marker.touch()
        archive.unlink()
    os.environ["PATH"] = str(node.parent) + os.pathsep + os.environ.get("PATH", "")
    os.environ["npm_config_cache"] = str(LOCAL / "cache" / "npm")
    return node, npm


def ensure_frontend(node: Path, npm: Path) -> None:
    dependencies = [FRONTEND / "package.json", FRONTEND / "package-lock.json", FRONTEND / ".npmrc"]
    install_key = fingerprint(dependencies) + NODE_VERSION + platform.system() + platform.machine()
    install_stamp = FRONTEND / "node_modules" / ".dishai-dependencies"
    if not current_stamp(install_stamp, install_key):
        print("Installing frontend dependencies...", flush=True)
        run(node, npm, "ci", "--no-audit", "--no-fund", cwd=FRONTEND)
        install_stamp.write_text(install_key, encoding="utf-8")
    inputs = dependencies + [FRONTEND / "index.html", FRONTEND / "vite.config.ts", FRONTEND / "tsconfig.json"]
    inputs += [p for p in (FRONTEND / "src").rglob("*") if p.is_file()]
    inputs += [p for p in (ROOT / "public").rglob("*") if p.is_file()]
    inputs += [ROOT / name for name in (".env", ".env.local", ".env.production", ".env.production.local")]
    build_key = fingerprint(inputs) + install_key
    # Vite also reads these from the environment, even when .env does not change.
    build_key += hashlib.sha256(json.dumps({k: v for k, v in os.environ.items() if k.startswith(("VITE_", "MAP_PROVIDER", "GOOGLE_MAPS_BROWSER_KEY"))}, sort_keys=True).encode()).hexdigest()
    build_stamp = FRONTEND / "dist" / ".dishai-build"
    if not current_stamp(build_stamp, build_key) or not (FRONTEND / "dist" / "index.html").exists():
        print("Building the app...", flush=True)
        run(node, npm, "run", "build", cwd=FRONTEND)
        build_stamp.write_text(build_key, encoding="utf-8")


@contextmanager
def launch_lock():
    """Keep repeated clicks from installing/building or launching twice."""
    with (LOCAL / "launcher.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
        else:
            try:
                yield True
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def running_url() -> str | None:
    try:
        url = json.loads(STATE.read_text(encoding="utf-8"))["url"]
        # Ignore proxy environment variables for our loopback readiness checks.
        if not url.startswith("http://127.0.0.1:"):
            return None
        with build_opener(ProxyHandler({})).open(url + "/api/health", timeout=1) as response:
            health = json.load(response)
        return url if health.get("app") == "dishai" and health.get("ok") else None
    except (OSError, ValueError, KeyError, URLError):
        return None


def serve(port: int, smoke_test: bool) -> None:
    import uvicorn
    sys.path.insert(0, str(ROOT))
    from backend.main import app
    if smoke_test:
        # Exercise startup/static serving without warmup's external API calls or fixture writes.
        app.router.on_startup.clear()
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
                raise
            sock.bind(("127.0.0.1", 0))
        actual_port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{actual_port}"
        STATE.write_text(json.dumps({"url": url}), encoding="utf-8")
        uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=actual_port)).run(sockets=[sock])


def start(args: argparse.Namespace) -> None:
    uv = Path(os.environ["DISHAI_UV"])
    python = ensure_python(uv)
    ensure_frontend(*ensure_node())
    STATE.unlink(missing_ok=True)
    command = [str(python), "-X", "utf8", "-u", str(Path(__file__).resolve()), "--serve", "--port", str(args.port)]
    if args.smoke_test:
        command.append("--smoke-test")
    child = subprocess.Popen(command, cwd=ROOT)
    try:
        deadline = time.monotonic() + 120
        while child.poll() is None:
            url = running_url()
            if url:
                print(f"\nDishAI is ready: {url}\nKeep this window open. Press Ctrl+C to stop.\n", flush=True)
                if args.smoke_test:
                    with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
                        if b'<div id="root">' not in response.read():
                            raise RuntimeError("The built frontend was not served.")
                    print("Smoke test passed: API and frontend are available.", flush=True)
                    return
                if not args.no_browser:
                    webbrowser.open(url)
                if child.wait() != 0:
                    raise RuntimeError("The local server stopped unexpectedly. See its output above.")
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("The app did not become ready within two minutes. See the output above.")
            time.sleep(0.25)
        raise RuntimeError("The server could not start. See the output above.")
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        STATE.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Start without opening a browser")
    parser.add_argument("--smoke-test", action="store_true", help="Set up, verify API/UI serving without external API calls, then stop")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    os.chdir(ROOT)
    LOCAL.mkdir(exist_ok=True)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONUNBUFFERED"] = "1"
    try:
        if args.serve:
            serve(args.port, args.smoke_test)
        else:
            with launch_lock() as acquired:
                if acquired:
                    start(args)
                else:
                    url = running_url()
                    if url:
                        print(f"DishAI is already running: {url}")
                        if not args.no_browser and not args.smoke_test:
                            webbrowser.open(url)
                    else:
                        print("DishAI is already starting. Please wait for the first launcher window.")
        return 0
    except KeyboardInterrupt:
        print("\nDishAI stopped.")
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError, KeyError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
