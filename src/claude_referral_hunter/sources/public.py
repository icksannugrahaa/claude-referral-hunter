"""Anonymous sources (no account needed). Low hit-rate, but free and safe."""

from __future__ import annotations

from typing import Iterable
from urllib.parse import quote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .. import http
from ..models import Finding, extract_links, parse_ts

KEYWORDS = ("referral", "trial", "invite", "free week", "7 day", "7-day", "claude pro", "claude.ai/")


def _relevant(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in KEYWORDS)


def hackernews(queries: Iterable[str] = ("claude.ai/referral", "claude referral", "claude pro trial")) -> list[Finding]:
    out: list[Finding] = []
    for q in queries:
        resp = http.get(
            f"https://hn.algolia.com/api/v1/search_by_date?query={quote(q)}&tags=(story,comment)&hitsPerPage=30"
        )
        if not resp:
            continue
        for hit in resp.json().get("hits", []):
            blob = " ".join(str(hit.get(k) or "") for k in ("title", "story_text", "comment_text", "url"))
            for link in extract_links(blob):
                out.append(Finding(
                    link, "hackernews", (hit.get("title") or hit.get("comment_text") or "")[:200],
                    posted_ts=parse_ts(hit.get("created_at_i") or hit.get("created_at")),
                    author=hit.get("author") or "",
                    permalink=f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                ))
    return out


def reddit_rss(subs: Iterable[str] = ("ClaudeAI", "ClaudeCode", "claude", "Anthropic")) -> list[Finding]:
    """Reddit Atom feeds. Rate-limited (HTTP 429) quickly — prefer the OAuth source."""
    out: list[Finding] = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for sub in subs:
        resp = http.get(f"https://www.reddit.com/r/{sub}/new/.rss?limit=50")
        if not resp:
            continue
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            continue
        for entry in root.findall("a:entry", ns):
            title = entry.findtext("a:title", default="", namespaces=ns) or ""
            content = entry.findtext("a:content", default="", namespaces=ns) or ""
            ts = parse_ts(entry.findtext("a:published", default="", namespaces=ns)
                          or entry.findtext("a:updated", default="", namespaces=ns))
            link_el = entry.find("a:link", ns)
            for link in extract_links(f"{title}\n{content}"):
                out.append(Finding(link, f"reddit:{sub}", title[:200], posted_ts=ts,
                                   permalink=link_el.get("href", "") if link_el is not None else ""))
    return out


def github(queries: Iterable[str] = ("claude.ai/referral",)) -> list[Finding]:
    out: list[Finding] = []
    for q in queries:
        resp = http.get(
            f"https://api.github.com/search/issues?q={quote(q)}&sort=created&order=desc&per_page=20",
            headers={"Accept": "application/vnd.github+json"},
        )
        if not resp:
            continue
        for item in resp.json().get("items", []):
            blob = f"{item.get('title') or ''}\n{item.get('body') or ''}"
            for link in extract_links(blob):
                out.append(Finding(link, "github", (item.get("title") or "")[:200],
                                   posted_ts=parse_ts(item.get("created_at")),
                                   author=(item.get("user") or {}).get("login", ""),
                                   permalink=item.get("html_url", "")))
    return out


def lobsters() -> list[Finding]:
    resp = http.get("https://lobste.rs/newest.rss")
    if not resp:
        return []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []
    out: list[Finding] = []
    for item in root.iter("item"):
        title = item.findtext("title", default="")
        desc = item.findtext("description", default="")
        for link in extract_links(f"{title}\n{desc}"):
            out.append(Finding(link, "lobsters", title[:200], posted_ts=parse_ts(item.findtext("pubDate")),
                               permalink=item.findtext("link", default="")))
    return out


def mastodon(instance: str = "mastodon.social", queries: Iterable[str] = ("claude referral", "claude.ai/referral")) -> list[Finding]:
    out: list[Finding] = []
    for q in queries:
        resp = http.get(f"https://{instance}/api/v2/search?q={quote(q)}&resolve=false&limit=20&type=statuses")
        if not resp:
            continue
        for st in resp.json().get("statuses", []):
            text = BeautifulSoup(st.get("content") or "", "html.parser").get_text(" ", strip=True)
            links = [c.get("url", "") for c in [st.get("card") or {}]]
            for link in extract_links(text + "\n" + "\n".join(links)):
                out.append(Finding(link, "mastodon", text[:200], posted_ts=parse_ts(st.get("created_at")),
                                   author=(st.get("account") or {}).get("acct", ""), permalink=st.get("url", "")))
    return out


def lemmy(instance: str = "lemmy.world", queries: Iterable[str] = ("claude referral",)) -> list[Finding]:
    out: list[Finding] = []
    for q in queries:
        resp = http.get(f"https://{instance}/api/v3/search?q={quote(q)}&type_=Posts&sort=New&limit=20")
        if not resp:
            continue
        for entry in resp.json().get("posts", []):
            post = entry.get("post") or {}
            body = f"{post.get('name') or ''}\n{post.get('body') or ''}\n{post.get('url') or ''}"
            for link in extract_links(body):
                out.append(Finding(link, "lemmy", (post.get("name") or "")[:200],
                                   posted_ts=parse_ts(post.get("published")), permalink=post.get("ap_id", "")))
    return out


def telegram_public(channels: Iterable[str] = ("claudeai",)) -> list[Finding]:
    """Public channel web previews (https://t.me/s/<channel>), no account needed."""
    out: list[Finding] = []
    for ch in channels:
        resp = http.get(f"https://t.me/s/{ch}")
        if not resp:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for wrap in soup.select(".tgme_widget_message")[-30:]:
            msg = wrap.select_one(".tgme_widget_message_text")
            if not msg:
                continue
            blob = msg.get_text("\n") + "\n" + "\n".join(a.get("href", "") for a in msg.find_all("a", href=True))
            if not _relevant(blob):
                continue
            time_el = wrap.select_one(".tgme_widget_message_date time")
            for link in extract_links(blob):
                out.append(Finding(link, f"telegram:{ch}", msg.get_text(" ", strip=True)[:200],
                                   posted_ts=parse_ts(time_el.get("datetime") if time_el else None)))
    return out


def fourchan(board: str = "g") -> list[Finding]:
    resp = http.get(f"https://a.4cdn.org/{board}/catalog.json")
    if not resp:
        return []
    out: list[Finding] = []
    for page in resp.json() if isinstance(resp.json(), list) else []:
        for th in page.get("threads") or []:
            raw = f"{th.get('sub') or ''} {th.get('com') or ''}"
            text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
            if not _relevant(text):
                continue
            for link in extract_links(text + "\n" + raw):
                out.append(Finding(link, f"4chan:{board}", text[:200], posted_ts=parse_ts(th.get("time"))))
    return out


# name → (fetcher, default interval seconds)
PUBLIC_SOURCES = {
    "hackernews": (hackernews, 30),
    "reddit_rss": (reddit_rss, 120),
    "github": (github, 180),
    "lobsters": (lobsters, 300),
    "mastodon": (mastodon, 120),
    "lemmy": (lemmy, 300),
    "telegram_public": (telegram_public, 60),
    "fourchan": (fourchan, 300),
}
