from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class RouteHealthStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rows: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._rows = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            self._rows = {}

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._rows, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def mark_success(self, route: str, elapsed: float, size: int = 0) -> None:
        now = time.time()
        with self._lock:
            row = self._rows.setdefault(route, {})
            successes = int(row.get("successes") or 0) + 1
            previous = float(row.get("avg_seconds") or 0.0)
            row.update({
                "successes": successes,
                "fails": max(0, int(row.get("fails") or 0) - 1),
                "last_success": now,
                "last_error": "",
                "last_kind": "",
                "last_size": int(size or 0),
                "avg_seconds": elapsed if previous <= 0 else previous * 0.7 + float(elapsed) * 0.3,
            })
            self._save_locked()

    def mark_failure(self, route: str, kind: str, message: str) -> None:
        now = time.time()
        with self._lock:
            row = self._rows.setdefault(route, {})
            row.update({
                "fails": int(row.get("fails") or 0) + 1,
                "last_failure": now,
                "last_kind": str(kind or "unknown"),
                "last_error": str(message or "")[:500],
            })
            self._save_locked()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._rows.items()}
