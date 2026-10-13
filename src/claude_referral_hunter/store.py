"""Seen-link store (JSON file) with per-link state.

States:
  stale     spotted too late (older than the freshness window). Does NOT block a
            later fresh sighting of the same code — e.g. X search lagging behind,
            then a relay bot posting it a minute later.
  seen      recorded during warm-up / without notifying. Blocks.
  notified  delivered to the notifiers. Blocks.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from .models import Finding

STALE = "stale"
SEEN = "seen"
NOTIFIED = "notified"


class SeenStore:
    def __init__(self, path: Optional[str], max_entries: int = 5000) -> None:
        self.path = path
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self._data = dict(raw.get("seen") or {})
        except (OSError, ValueError):
            self._data = {}

    def blocks(self, finding: Finding) -> bool:
        """True when this code was already notified / recorded (not merely stale)."""
        with self._lock:
            entry = self._data.get(finding.key)
        return entry is not None and entry.get("state") != STALE

    def known(self, finding: Finding) -> bool:
        with self._lock:
            return finding.key in self._data

    def mark(self, finding: Finding, state: str) -> None:
        with self._lock:
            self._data[finding.key] = {
                "url": finding.url,
                "source": finding.source,
                "ts": int(time.time()),
                "state": state,
            }

    def get(self, finding: Finding) -> Optional[dict]:
        with self._lock:
            entry = self._data.get(finding.key)
            return dict(entry) if entry else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            if len(self._data) > self.max_entries:
                keep = sorted(self._data.items(), key=lambda kv: kv[1].get("ts", 0))
                self._data = dict(keep[-int(self.max_entries * 0.8):])
            payload = {"seen": self._data}
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, self.path)
