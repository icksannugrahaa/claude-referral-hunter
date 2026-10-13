"""Findings and the pure helpers around them (link extraction, dedup key, time)."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

# Claude referral / invite URL shapes. Codes are case-sensitive.
REFERRAL_RE = re.compile(
    r"https?://(?:www\.)?claude\.(?:ai|com)/(?:referral|r|invite)/[A-Za-z0-9_\-]+",
    re.IGNORECASE,
)
_CODE_RE = re.compile(r"claude\.(ai|com)/(referral|r|invite)/([A-Za-z0-9_\-]+)", re.IGNORECASE)


def extract_links(text: str) -> list[str]:
    """Unique referral URLs in ``text`` (query strings such as ?s=android dropped)."""
    seen: set[str] = set()
    out: list[str] = []
    for match in REFERRAL_RE.findall(text or ""):
        url = match.rstrip(").,;!?\"'")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def referral_key(url: str) -> str:
    """Dedup basis ``<kind>/<code>``: the same code shared twice counts once."""
    m = _CODE_RE.search(url or "")
    if not m:
        return (url or "").lower()
    return f"{m.group(2).lower()}/{m.group(3)}"


def canonical_url(url: str) -> str:
    m = _CODE_RE.search(url or "")
    if not m:
        return url
    return f"https://claude.{m.group(1).lower()}/{m.group(2).lower()}/{m.group(3)}"


def parse_ts(value: Any) -> Optional[float]:
    """Epoch seconds from epoch numbers, ISO 8601 or RFC 822 strings; else None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.isdigit():
        return float(text)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    # Threads/Graph API style: 2026-10-12T14:00:00+0000
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")
        return dt.timestamp()
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, IndexError):
        return None


@dataclass
class Finding:
    """One referral link spotted by a source."""

    url: str
    source: str
    context: str = ""
    posted_ts: Optional[float] = None
    author: str = ""
    permalink: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.url = canonical_url(self.url)

    @property
    def key(self) -> str:
        return hashlib.sha1(referral_key(self.url).encode()).hexdigest()

    @property
    def code(self) -> str:
        return referral_key(self.url).split("/", 1)[-1]

    def age_sec(self, now: Optional[float] = None) -> Optional[float]:
        if self.posted_ts is None:
            return None
        return (now if now is not None else time.time()) - self.posted_ts

    def is_fresh(self, max_age_sec: float, allow_undated: bool = False, now: Optional[float] = None) -> bool:
        age = self.age_sec(now)
        if age is None:
            return allow_undated
        return -60 <= age <= max_age_sec  # small negative tolerance for clock skew

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "code": self.code,
            "source": self.source,
            "context": self.context,
            "posted_ts": self.posted_ts,
            "age_sec": None if self.posted_ts is None else round(self.age_sec() or 0),
            "author": self.author,
            "permalink": self.permalink,
        }
