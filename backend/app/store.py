"""Tiny, crash-safe JSON persistence (atomic writes, thread-safe)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class JsonStore:
    def __init__(self, path: Path, default: Callable[[], Any]):
        self.path = path
        self._default = default
        self._lock = threading.RLock()
        self._data = self._read()

    def _read(self) -> Any:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return self._default()
        except json.JSONDecodeError:
            # Keep a copy of the broken file and start fresh instead of crashing the panel.
            broken = self.path.with_suffix(self.path.suffix + ".broken")
            os.replace(self.path, broken)
            return self._default()

    @property
    def data(self) -> Any:
        return self._data

    def save(self, data: Any | None = None) -> None:
        with self._lock:
            if data is not None:
                self._data = data
            atomic_write_text(self.path, json.dumps(self._data, indent=2, ensure_ascii=False))
