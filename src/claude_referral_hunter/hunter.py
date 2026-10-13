"""The scan loop: schedule sources, fetch in parallel, dedup, notify."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Callable, Iterable, Optional, Sequence

from .models import Finding
from .notifiers.base import Notifier
from .sources.base import Source
from .store import NOTIFIED, SEEN, STALE, SeenStore

logger = logging.getLogger("claude_referral_hunter")


class Hunter:
    """Polls sources and pushes fresh, never-seen referral links to notifiers.

    Each source has its own interval. Due sources run in parallel; a cycle waits
    at most ``cycle_timeout`` — a source stuck on the network keeps running in
    the background and its result is used by a later cycle, so one slow source
    never delays the real-time ones.
    """

    def __init__(
        self,
        sources: Sequence[Source],
        notifiers: Sequence[Notifier] = (),
        *,
        store: Optional[SeenStore] = None,
        fresh_max_age: float = 600,
        allow_undated: bool = False,
        workers: int = 6,
        cycle_timeout: float = 60,
        tick: float = 5,
        on_findings: Optional[Callable[[list[Finding]], None]] = None,
    ) -> None:
        if not sources:
            raise ValueError("Hunter needs at least one source")
        self.sources = list(sources)
        self.notifiers = list(notifiers)
        # `is not None`: an empty SeenStore is falsy (len 0) but must be kept.
        self.store = store if store is not None else SeenStore(None)
        self.fresh_max_age = fresh_max_age
        self.allow_undated = allow_undated
        self.cycle_timeout = cycle_timeout
        self.tick = tick
        self.on_findings = on_findings
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="crh-fetch")
        self._inflight: dict[str, Future] = {}
        self._last_fetch: dict[str, float] = {}
        self._stop = threading.Event()

    # ── scheduling ───────────────────────────────────────────────────────
    def due(self, now: Optional[float] = None) -> list[Source]:
        now = time.time() if now is None else now
        return [s for s in self.sources if now - self._last_fetch.get(s.name, 0.0) >= s.interval]

    def _fetch(self, sources: Iterable[Source]) -> dict[str, list[Finding] | Exception]:
        for src in sources:
            if src.name in self._inflight:
                continue  # still running (or finished, unread) from an earlier cycle
            self._inflight[src.name] = self._pool.submit(lambda s=src: list(s.fetch()))
        waiting = {fut: name for name, fut in self._inflight.items()}
        if waiting:
            wait(waiting, timeout=self.cycle_timeout)
        results: dict[str, list[Finding] | Exception] = {}
        for fut, name in waiting.items():
            if not fut.done():
                logger.warning("source %s still running after %ds — continuing next cycle", name, self.cycle_timeout)
                continue
            del self._inflight[name]
            try:
                results[name] = fut.result()
            except Exception as exc:  # a source's own bug must not stop the loop
                results[name] = exc
        return results

    # ── one cycle ────────────────────────────────────────────────────────
    def scan(self, *, notify: bool = True, require_fresh: bool = True,
             sources: Optional[Sequence[Source]] = None) -> list[Finding]:
        """Fetch ``sources`` (default: all), return the new fresh findings.

        ``notify=False, require_fresh=False`` is the warm-up: everything already
        out there is recorded as seen so a fresh start does not spam.
        """
        now = time.time()
        results = self._fetch(self.sources if sources is None else sources)
        fresh: list[Finding] = []
        stale = 0
        for name, res in results.items():
            if isinstance(res, Exception):
                logger.warning("source %s failed: %s", name, res)
                continue
            self._last_fetch[name] = now
            for f in res:
                if self.store.blocks(f) or any(f.key == g.key for g in fresh):
                    continue
                if require_fresh and not f.is_fresh(self.fresh_max_age, self.allow_undated):
                    if not self.store.known(f):  # count/log each stale code once
                        stale += 1
                        self.store.mark(f, STALE)
                    continue
                self.store.mark(f, NOTIFIED if notify else SEEN)
                fresh.append(f)
        self.store.save()
        if stale:
            logger.info("skipped %d link(s) older than %ds", stale, self.fresh_max_age)
        if fresh and notify:
            self._deliver(fresh)
        return fresh

    def _deliver(self, findings: list[Finding]) -> None:
        logger.info("%d fresh link(s): %s", len(findings), ", ".join(f.code for f in findings))
        if self.on_findings:
            try:
                self.on_findings(findings)
            except Exception as exc:
                logger.error("on_findings callback failed: %s", exc)
        for notifier in self.notifiers:
            try:
                notifier.send(findings)
            except Exception as exc:  # one broken channel must not block the others
                logger.error("notifier %s failed: %s", notifier.name, exc)

    # ── loop ─────────────────────────────────────────────────────────────
    def warm_up(self) -> int:
        n = len(self.scan(notify=False, require_fresh=False))
        logger.info("warm-up: %d existing link(s) marked as seen", n)
        return n

    def run_forever(self, *, warm_up: bool = True) -> None:
        logger.info("hunter start — sources: %s", ", ".join(f"{s.name}/{s.interval}s" for s in self.sources))
        if warm_up:
            try:
                self.warm_up()
            except Exception as exc:
                logger.warning("warm-up failed: %s", exc)
        cycle = 0
        while not self._stop.is_set():
            cycle += 1
            due = self.due()
            if due:
                try:
                    found = self.scan(sources=due)
                    logger.info("[cycle %d] %s → %d fresh", cycle, ",".join(s.name for s in due), len(found))
                except Exception as exc:
                    logger.exception("cycle %d failed: %s", cycle, exc)
            self._stop.wait(self.tick)

    def stop(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False)
