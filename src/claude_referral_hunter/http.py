"""Tiny HTTP helper shared by the anonymous sources."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger("claude_referral_hunter")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 claude-referral-hunter"
)
TIMEOUT = 15
_WARN_EVERY = 600
_warned: dict[str, float] = {}
_last_error = threading.local()


def _warn(url: str, reason: str) -> None:
    """Warn once per host per 10 min (a silent failure looks like "no links")."""
    _last_error.value = reason
    host = urlsplit(url).netloc
    now = time.time()
    if now - _warned.get(host, 0) >= _WARN_EVERY:
        _warned[host] = now
        logger.warning("GET %s failed: %s", host, reason)


def last_error() -> str:
    """Last failure seen by get() on this thread (used by `probe`)."""
    return getattr(_last_error, "value", "")


def clear_error() -> None:
    _last_error.value = ""


def get(url: str, *, headers: Optional[dict] = None, **kw) -> Optional[requests.Response]:
    """GET returning the response on HTTP 200, else None (never raises)."""
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    try:
        resp = requests.get(url, headers=hdrs, timeout=kw.pop("timeout", TIMEOUT), **kw)
    except requests.RequestException as exc:
        _warn(url, f"{type(exc).__name__}: {str(exc)[:160]}")
        return None
    if resp.status_code != 200:
        _warn(url, f"HTTP {resp.status_code}")
        return None
    return resp
