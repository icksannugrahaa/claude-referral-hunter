"""Add your own source (any function returning Finding objects) next to the built-in ones."""

import time

from claude_referral_hunter import ConsoleNotifier, Finding, Hunter, PUBLIC_SOURCES, SeenStore, Source, extract_links
from claude_referral_hunter import http


def my_forum() -> list[Finding]:
    resp = http.get("https://forum.example.com/latest.json")
    if not resp:
        return []
    out = []
    for post in resp.json().get("posts", []):
        for link in extract_links(post.get("body", "")):
            out.append(Finding(link, "my-forum", post.get("title", ""), posted_ts=post.get("created_at")))
    return out


if __name__ == "__main__":
    hn, hn_interval = PUBLIC_SOURCES["hackernews"]
    Hunter(
        [Source("hackernews", hn, hn_interval), Source("my-forum", my_forum, 60)],
        [ConsoleNotifier(tz_offset_hours=7)],
        store=SeenStore("state/seen.json"),
    ).run_forever()
