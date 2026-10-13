"""Notifier interface and message rendering shared by all channels."""

from __future__ import annotations

import html
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, Sequence

from ..models import Finding


class Notifier(Protocol):
    """Anything with a ``name`` and ``send(findings)`` can be a notifier."""

    name: str

    def send(self, findings: Sequence[Finding]) -> None: ...


def _age(f: Finding, now: float) -> str:
    age = f.age_sec(now)
    if age is None:
        return "age unknown"
    age = max(0, int(age))
    return f"{age}s ago" if age < 120 else f"{age // 60} min ago"


def _clock(ts: Optional[float], tz: timezone) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def render(findings: Sequence[Finding], style: str = "plain", *, tz_offset_hours: float = 0,
           limit: int = 10) -> str:
    """Human message for a batch of findings.

    style: ``plain`` | ``whatsapp`` (*bold* _italic_) | ``telegram_html`` |
    ``markdown`` (Discord/Slack).
    """
    now = time.time()
    tz = timezone(timedelta(hours=tz_offset_hours))
    bold = {"whatsapp": ("*", "*"), "telegram_html": ("<b>", "</b>"), "markdown": ("**", "**")}.get(style, ("", ""))
    ital = {"whatsapp": ("_", "_"), "telegram_html": ("<i>", "</i>"), "markdown": ("_", "_")}.get(style, ("", ""))
    esc = html.escape if style == "telegram_html" else (lambda s: s)

    head = f"{bold[0]}🎁 Claude referral link{'s' if len(findings) > 1 else ''} (fresh){bold[1]}"
    lines = [head, ""]
    for f in list(findings)[:limit]:
        who = f" {esc(f.author)}" if f.author else ""
        lines.append(f"• {bold[0]}{esc(f.source)}{bold[1]}{who} — {ital[0]}{_age(f, now)}{ital[1]}")
        when = _clock(f.posted_ts, tz)
        if when:
            lines.append(f"  🕐 {when}")
        if f.context:
            ctx = f.context if len(f.context) <= 140 else f.context[:137] + "..."
            lines.append(f"  {ital[0]}{esc(ctx)}{ital[1]}")
        lines.append(f"  🔗 {f.url}")
        if f.permalink:
            lines.append(f"  ↪ {f.permalink}")
        lines.append("")
    if len(findings) > limit:
        lines.append(f"…and {len(findings) - limit} more")
    return "\n".join(lines).rstrip()


def split_text(text: str, limit: int) -> list[str]:
    """Split on line boundaries so each chunk fits a channel's size limit."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
