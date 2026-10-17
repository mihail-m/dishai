"""10-minute TTL cache with stale-then-fixture fallback."""
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("inversion_watch")

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TTL_SECONDS = 600


class Cache:
    def __init__(self, ttl: int = TTL_SECONDS, fixture_dir: Path = FIXTURE_DIR):
        self.ttl = ttl
        self.fixture_dir = fixture_dir
        self._store: dict[str, tuple[float, Any]] = {}

    def clear(self) -> None:
        self._store.clear()

    def expire(self, keys: tuple[str, ...]) -> None:
        """Force the next read upstream, retaining the last good value for outages."""
        for key in keys:
            if key in self._store:
                self._store[key] = (0.0, self._store[key][1])

    async def get(self, key: str, fetcher: Callable[[], Awaitable[Any]], persist: bool = True) -> tuple[Any, str]:
        """Return (data, source). source is one of: live, cache, stale, fixture.

        persist=False keeps the data in memory only (no fixture is read or written), for sources whose terms forbid
        archiving or redistributing their data.
        """
        now = time.time()
        hit = self._store.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1], "cache"
        try:
            data = await fetcher()
        except Exception as exc:  # noqa: BLE001 - any upstream failure falls back
            log.warning("upstream %s failed: %s", key, type(exc).__name__ if not persist else repr(exc))
            if hit:
                return hit[1], "stale"
            fixture = self._load_fixture(key) if persist else None
            if fixture is not None:
                return fixture, "fixture"
            raise
        self._store[key] = (now, data)
        if persist:
            self._save_fixture(key, data)
        return data, "live"

    def _fixture_path(self, key: str) -> Path:
        return self.fixture_dir / f"{key}.json"

    def _save_fixture(self, key: str, data: Any) -> None:
        try:
            self.fixture_dir.mkdir(parents=True, exist_ok=True)
            self._fixture_path(key).write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            log.warning("could not save fixture %s: %r", key, exc)

    def _load_fixture(self, key: str) -> Any | None:
        p = self._fixture_path(key)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None
