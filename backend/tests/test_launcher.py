"""The local launcher's repeat-click, readiness and incremental-build behavior."""
import json
from pathlib import Path

import pytest

from scripts import launch


def test_repeated_launch_is_locked_and_exception_releases_it(monkeypatch, tmp_path):
    monkeypatch.setattr(launch, "LOCAL", tmp_path)
    with pytest.raises(RuntimeError):
        with launch.launch_lock() as first:
            assert first
            with launch.launch_lock() as second:
                assert not second
            raise RuntimeError("setup failed")
    with launch.launch_lock() as retry:
        assert retry


def test_unrelated_local_server_is_not_treated_as_dishai(monkeypatch, tmp_path):
    state = tmp_path / "server.json"
    state.write_text(json.dumps({"url": "http://127.0.0.1:8000"}))
    monkeypatch.setattr(launch, "STATE", state)

    class OtherServer:
        def open(self, *args, **kwargs):
            from io import BytesIO
            return BytesIO(b'{"ok": true, "app": "another-app"}')

    monkeypatch.setattr(launch, "build_opener", lambda *args: OtherServer())
    assert launch.running_url() is None


def test_incremental_build_notices_source_and_env_changes(monkeypatch, tmp_path):
    frontend = tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)
    (tmp_path / "public").mkdir()
    source = frontend / "src" / "App.tsx"
    source.write_text("first")
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    monkeypatch.setattr(launch, "FRONTEND", frontend)
    calls = []

    def fake_run(*command, **kwargs):
        calls.append(command[2:])
        (frontend / "node_modules").mkdir(exist_ok=True)
        (frontend / "dist").mkdir(exist_ok=True)
        (frontend / "dist" / "index.html").write_text('<div id="root"></div>')

    monkeypatch.setattr(launch, "run", fake_run)
    for _ in range(2):
        launch.ensure_frontend(Path("node"), Path("npm"))
    assert [c[0] for c in calls] == ["ci", "run"]
    source.write_text("second")
    launch.ensure_frontend(Path("node"), Path("npm"))
    monkeypatch.setenv("GOOGLE_MAPS_BROWSER_KEY", "test-browser-key")
    launch.ensure_frontend(Path("node"), Path("npm"))
    assert [c[0] for c in calls] == ["ci", "run", "run", "run"]


@pytest.mark.parametrize("machine,target", [("arm64", "darwin-arm64"), ("x86_64", "darwin-x64")])
def test_both_mac_architectures_have_native_node_distribution(machine, target):
    assert launch.node_distribution("Darwin", machine) == (target, "tar.gz")
