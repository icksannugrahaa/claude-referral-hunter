"""Source interface: anything with a name, an interval and a fetch()."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from ..models import Finding

logger = logging.getLogger("claude_referral_hunter")


@dataclass
class Source:
    """A place to look for referral links.

    ``fetch`` returns findings (may be empty) and may raise; the hunter logs
    the error and retries on the next interval.
    """

    name: str
    fetch: Callable[[], Iterable[Finding]]
    interval: float = 60


class Backoff:
    """Skip a source until a deadline, logging the reason once per hold."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.until = 0.0

    def active(self) -> bool:
        return time.time() < self.until

    def hold(self, seconds: float, reason: str) -> None:
        self.until = time.time() + max(5.0, seconds)
        logger.warning("source %s paused %ds: %s", self.name, int(seconds), reason)
