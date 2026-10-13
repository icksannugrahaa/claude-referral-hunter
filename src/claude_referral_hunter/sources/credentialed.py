"""Near-real-time sources that need an account or API credentials.

  tg_relay     Telegram *user* account (Telethon) reading chats/bots/channels
               that already relay referral links. Fastest and safest signal.
  x            X/Twitter "Latest" search via twikit with a spare account's
               cookies. Against X's ToS — the account can be suspended.
  reddit       Reddit OAuth app-only API (site-wide search + new posts/comments).
  threads_api  Threads ``keyword_search``. Other people's posts only after Meta
               App Review grants ``threads_keyword_search``.

Parsing helpers (``*_findings``) are pure and unit-tested.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Optional

import requests

from .. import http
from ..models import Finding, extract_links, parse_ts
from .base import Backoff


class AsyncRunner:
    """One background event loop for the asyncio clients (Telethon, twikit)."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="crh-async", daemon=True).start()
                self._loop = loop
            return self._loop

    def run(self, coro, timeout: float = 45.0):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure()).result(timeout)


RUNNER = AsyncRunner()


def _squash(text: str, limit: int = 200) -> str:
    return " ".join((text or "").split())[:limit]


# ── Telegram relay ───────────────────────────────────────────────────────

def tg_message_findings(msg: Any, chat: str) -> list[Finding]:
    """Links in one Telethon message: text, hidden link entities, web preview."""
    text = getattr(msg, "message", None) or ""
    urls: list[str] = []
    for ent in getattr(msg, "entities", None) or []:
        url = getattr(ent, "url", None)
        if url:
            urls.append(url)
        elif type(ent).__name__ == "MessageEntityUrl":
            urls.append(text[ent.offset: ent.offset + ent.length])
    page = getattr(getattr(msg, "media", None), "webpage", None)
    if getattr(page, "url", None):
        urls.append(page.url)
    date = getattr(msg, "date", None)
    ts = date.timestamp() if date is not None else None
    return [Finding(link, f"tg_relay:{chat}", _squash(text), posted_ts=ts)
            for link in extract_links(text + "\n" + "\n".join(urls))]


class TelegramRelay:
    def __init__(self, api_id: int, api_hash: str, session: str, chats: list[str]) -> None:
        self.api_id, self.api_hash, self.session, self.chats = api_id, api_hash, session, chats
        self.client = None
        self.backoff = Backoff("tg_relay")

    async def _fetch(self) -> list[Finding]:
        from telethon import TelegramClient  # optional dependency

        if self.client is None:
            client = TelegramClient(self.session, self.api_id, self.api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                raise PermissionError("Telegram session not logged in — run: claude-referral-hunter telegram-login")
            self.client = client
        elif not self.client.is_connected():
            await self.client.connect()
        out: list[Finding] = []
        for chat in self.chats:
            entity = int(chat) if chat.lstrip("-").isdigit() else chat
            for msg in await self.client.get_messages(entity, limit=20):
                out.extend(tg_message_findings(msg, chat.lstrip("@")))
        return out

    def fetch(self) -> list[Finding]:
        if self.backoff.active():
            return []
        try:
            return RUNNER.run(self._fetch())
        except PermissionError as exc:
            self.backoff.hold(600, str(exc))
        except Exception as exc:
            self.backoff.hold(float(getattr(exc, "seconds", 0) or 60), f"{type(exc).__name__}: {exc}"[:200])
        return []


# ── X / Twitter (twikit) ─────────────────────────────────────────────────

# X rotates GraphQL query ids. Verified 2026-10-13; override with X_SEARCH_QID
# when the log says "SearchTimeline query id expired (404)" — copy the new one
# from x.com DevTools → Network → SearchTimeline.
X_SEARCH_QID_DEFAULT = "irezafrnzav4Ulf1vDkEBg"
X_SEARCH_FEATURES = {
    "articles_preview_enabled": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_profile_redirect_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "rweb_video_screen_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_awards_web_tipping_enabled": False,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "tweet_with_visibility_results_prefer_gql_media_interstitial_enabled": False,
    "tweetypie_unmention_optimization_enabled": True,
    "verified_phone_label_enabled": False,
    "view_counts_everywhere_api_enabled": True,
    "premium_content_api_read_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": False,
}


def x_tweet_findings(tweet: Any) -> list[Finding]:
    """Links in one twikit Tweet (t.co links expanded through its url entities)."""
    text = getattr(tweet, "full_text", None) or getattr(tweet, "text", None) or ""
    expanded = [
        (u.get("expanded_url") or u.get("url") or "") if isinstance(u, dict) else str(u)
        for u in (getattr(tweet, "urls", None) or [])
    ]
    created = getattr(tweet, "created_at_datetime", None)
    ts = created.timestamp() if created is not None else parse_ts(getattr(tweet, "created_at", None))
    handle = getattr(getattr(tweet, "user", None), "screen_name", "") or ""
    tid = getattr(tweet, "id", "") or ""
    return [Finding(link, "x", _squash(text), posted_ts=ts, author=f"@{handle}" if handle else "",
                    permalink=f"https://x.com/{handle}/status/{tid}" if handle and tid else "")
            for link in extract_links(text + "\n" + "\n".join(expanded))]


class XSearch:
    def __init__(self, cookies_file: str, queries: list[str], *, qid: str = "", features_json: str = "",
                 timeout: float = 20) -> None:
        self.cookies_file, self.queries = cookies_file, queries
        self.qid = qid or X_SEARCH_QID_DEFAULT
        self.features = dict(X_SEARCH_FEATURES)
        if features_json:
            parsed = json.loads(features_json)
            if isinstance(parsed, dict):
                self.features.update(parsed)
        self.timeout = timeout
        self.client = None
        self.backoff = Backoff("x")

    def _patch(self) -> None:
        from twikit.client import gql  # optional dependency

        gql.Endpoint.SEARCH_TIMELINE = gql.Endpoint.url(f"{self.qid}/SearchTimeline")
        gql.FEATURES = {**gql.FEATURES, **self.features}

    async def _fetch(self) -> list[Finding]:
        from twikit import Client

        if self.client is None:
            self._patch()
            with open(self.cookies_file, encoding="utf-8") as fh:
                cookies = json.load(fh)
            client = Client("en-US", timeout=self.timeout)
            client.set_cookies(cookies)
            self.client = client
        out: list[Finding] = []
        for q in self.queries:
            for tweet in await self.client.search_tweet(q, "Latest", count=20):
                out.extend(x_tweet_findings(tweet))
        return out

    def fetch(self) -> list[Finding]:
        if self.backoff.active():
            return []
        try:
            return RUNNER.run(self._fetch())
        except Exception as exc:
            name = type(exc).__name__
            if name == "TooManyRequests":
                reset = getattr(exc, "rate_limit_reset", None)
                self.backoff.hold((reset - time.time()) if reset else 300, "rate limited by X")
            elif name == "NotFound":
                self.client = None
                self.backoff.hold(1800, "SearchTimeline query id expired (404) — set X_SEARCH_QID (see README)")
            elif name == "BadRequest":
                self.backoff.hold(1800, f"400 from X (feature flags changed? set X_SEARCH_FEATURES) {exc}"[:200])
            elif name in ("Unauthorized", "Forbidden", "AccountSuspended", "AccountLocked"):
                self.client = None
                self.backoff.hold(900, f"{name}: X cookies invalid or account restricted")
            else:
                self.backoff.hold(60, f"{name}: {exc}"[:200])
        return []


# ── Reddit OAuth ─────────────────────────────────────────────────────────

def reddit_listing_findings(data: dict, label: str) -> list[Finding]:
    out: list[Finding] = []
    for child in ((data or {}).get("data") or {}).get("children") or []:
        item = child.get("data") or {}
        if child.get("kind") == "t1":
            blob = context = item.get("body") or ""
        else:
            blob = "\n".join(str(item.get(k) or "") for k in ("title", "selftext", "url"))
            context = item.get("title") or ""
        sub = item.get("subreddit") or label
        permalink = f"https://www.reddit.com{item['permalink']}" if item.get("permalink") else ""
        for link in extract_links(blob):
            out.append(Finding(link, f"reddit:{sub}", _squash(context), posted_ts=parse_ts(item.get("created_utc")),
                               author=f"u/{item['author']}" if item.get("author") else "", permalink=permalink))
    return out


class RedditOAuth:
    def __init__(self, client_id: str, client_secret: str, user_agent: str, subs: list[str]) -> None:
        self.client_id, self.client_secret, self.user_agent, self.subs = client_id, client_secret, user_agent, subs
        self._token, self._exp = "", 0.0
        self.backoff = Backoff("reddit")

    def _bearer(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.user_agent},
            timeout=http.TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token, self._exp = body["access_token"], time.time() + float(body.get("expires_in", 3600))
        return self._token

    def fetch(self) -> list[Finding]:
        if self.backoff.active():
            return []
        subs = "+".join(self.subs)
        endpoints = [
            ("search", "https://oauth.reddit.com/search", {"q": '"claude.ai/referral"', "sort": "new", "limit": 50}),
            ("new", f"https://oauth.reddit.com/r/{subs}/new", {"limit": 100}),
            ("comments", f"https://oauth.reddit.com/r/{subs}/comments", {"limit": 100}),
        ]
        out: list[Finding] = []
        try:
            headers = {"Authorization": f"bearer {self._bearer()}", "User-Agent": self.user_agent}
            for label, url, params in endpoints:
                resp = requests.get(url, headers=headers, params={**params, "raw_json": 1}, timeout=http.TIMEOUT)
                if resp.status_code == 429:
                    self.backoff.hold(float(resp.headers.get("x-ratelimit-reset") or 60), "429")
                    break
                if resp.status_code == 401:
                    self._token = ""
                    continue
                if resp.status_code == 200:
                    out.extend(reddit_listing_findings(resp.json(), label))
        except Exception as exc:
            self.backoff.hold(60, f"{type(exc).__name__}: {exc}"[:200])
        return out


# ── Threads keyword search ───────────────────────────────────────────────

def threads_search_findings(data: dict) -> list[Finding]:
    out: list[Finding] = []
    for post in (data or {}).get("data") or []:
        text = post.get("text") or ""
        user = post.get("username") or ""
        for link in extract_links(text):
            out.append(Finding(link, "threads", _squash(text), posted_ts=parse_ts(post.get("timestamp")),
                               author=f"@{user}" if user else "", permalink=post.get("permalink", "")))
    return out


class ThreadsSearch:
    def __init__(self, token: str, queries: list[str]) -> None:
        self.token, self.queries = token, queries
        self.backoff = Backoff("threads")

    def fetch(self) -> list[Finding]:
        if self.backoff.active():
            return []
        out: list[Finding] = []
        for q in self.queries:
            try:
                resp = requests.get(
                    "https://graph.threads.net/v1.0/keyword_search",
                    params={"q": q, "search_type": "RECENT", "access_token": self.token,
                            "fields": "id,text,permalink,timestamp,username"},
                    timeout=http.TIMEOUT,
                )
            except requests.RequestException as exc:
                self.backoff.hold(60, type(exc).__name__)
                break
            if resp.status_code == 200:
                out.extend(threads_search_findings(resp.json()))
            elif resp.status_code in (401, 403):
                self.backoff.hold(1800, f"HTTP {resp.status_code} (token / threads_keyword_search permission?)")
                break
            elif resp.status_code == 429:
                self.backoff.hold(900, "429 (2,200 queries / 24 h quota?)")
                break
        return out

