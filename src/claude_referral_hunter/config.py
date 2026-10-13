"""Build sources, notifiers and the Hunter from environment variables (.env).

Every channel and credentialed source switches on by itself when its
credentials are present — see .env.example for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .hunter import Hunter
from .notifiers.channels import (
    ConsoleNotifier,
    DiscordNotifier,
    OpenWANotifier,
    TelegramBotNotifier,
    WebhookNotifier,
    WhatsAppCloudNotifier,
)
from .sources.base import Source
from .sources.credentialed import RedditOAuth, TelegramRelay, ThreadsSearch, XSearch
from .sources.public import PUBLIC_SOURCES
from .store import SeenStore

DEFAULT_PUBLIC = "hackernews,github,mastodon,lemmy,telegram_public,lobsters,fourchan,reddit_rss"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in env(name, default).split(",") if v.strip()]


def env_float(name: str, default: float) -> float:
    raw = env(name)
    return float(raw) if raw else default


def env_bool(name: str, default: bool) -> bool:
    raw = env(name).lower()
    return default if not raw else raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    state_dir: str = "state"
    fresh_sec: float = 600
    allow_undated: bool = False
    workers: int = 6
    cycle_timeout: float = 60
    tick: float = 5
    tz_offset: float = 0
    public_sources: list[str] = field(default_factory=list)
    console: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            state_dir=env("CRH_STATE_DIR", "state"),
            fresh_sec=env_float("CRH_FRESH_SEC", 600),
            allow_undated=env_bool("CRH_ALLOW_UNDATED", False),
            workers=int(env_float("CRH_WORKERS", 6)),
            cycle_timeout=env_float("CRH_CYCLE_TIMEOUT", 60),
            tick=env_float("CRH_TICK", 5),
            tz_offset=env_float("CRH_TZ_OFFSET", 0),
            public_sources=env_list("CRH_SOURCES", DEFAULT_PUBLIC),
            console=env_bool("CRH_CONSOLE", True),
        )

    def path(self, *parts: str) -> str:
        return os.path.join(self.state_dir, *parts)


def _interval(name: str, default: float) -> float:
    return env_float(f"CRH_INTERVAL_{name.upper()}", default)


def build_sources(s: Settings) -> list[Source]:
    sources: list[Source] = []
    reddit_oauth = bool(env("REDDIT_CLIENT_ID") and env("REDDIT_CLIENT_SECRET"))
    for name in s.public_sources:
        if name not in PUBLIC_SOURCES:
            raise ValueError(f"unknown source {name!r} in CRH_SOURCES (known: {', '.join(PUBLIC_SOURCES)})")
        if name == "reddit_rss" and reddit_oauth:
            continue  # the OAuth source replaces the 429-prone RSS feeds
        fn, interval = PUBLIC_SOURCES[name]
        sources.append(Source(name, fn, _interval(name, interval)))

    tg_session = env("TG_SESSION", s.path("telegram"))
    if env("TELEGRAM_API_ID") and env("TELEGRAM_API_HASH") and env_list("TG_RELAY_CHATS"):
        if os.path.exists(tg_session + ".session"):
            relay = TelegramRelay(int(env("TELEGRAM_API_ID")), env("TELEGRAM_API_HASH"), tg_session,
                                  env_list("TG_RELAY_CHATS"))
            sources.append(Source("tg_relay", relay.fetch, _interval("tg_relay", 10)))

    cookies = env("X_COOKIES_FILE", s.path("x_cookies.json"))
    if os.path.exists(cookies):
        x = XSearch(cookies, env_list("X_QUERIES", "claude.ai/referral"), qid=env("X_SEARCH_QID"),
                    features_json=env("X_SEARCH_FEATURES"), timeout=env_float("X_TIMEOUT", 20))
        sources.append(Source("x", x.fetch, _interval("x", 30)))

    if reddit_oauth:
        reddit = RedditOAuth(env("REDDIT_CLIENT_ID"), env("REDDIT_CLIENT_SECRET"),
                             env("REDDIT_USER_AGENT", "linux:claude-referral-hunter:1.0"),
                             env_list("REDDIT_SUBS", "ClaudeAI,ClaudeCode,claude,Anthropic"))
        sources.append(Source("reddit", reddit.fetch, _interval("reddit", 20)))

    if env("THREADS_ACCESS_TOKEN"):
        threads = ThreadsSearch(env("THREADS_ACCESS_TOKEN"), env_list("THREADS_QUERIES", "claude.ai/referral"))
        sources.append(Source("threads", threads.fetch, _interval("threads", 60)))
    return sources


def build_notifiers(s: Settings) -> list:
    tz = s.tz_offset
    out: list = []
    if s.console:
        out.append(ConsoleNotifier(tz))
    if env("TELEGRAM_BOT_TOKEN") and env_list("TELEGRAM_CHAT_IDS"):
        out.append(TelegramBotNotifier(env("TELEGRAM_BOT_TOKEN"), env_list("TELEGRAM_CHAT_IDS"), tz))
    if env("WHATSAPP_CLOUD_TOKEN") and env("WHATSAPP_CLOUD_PHONE_NUMBER_ID") and env_list("WHATSAPP_CLOUD_TO"):
        out.append(WhatsAppCloudNotifier(
            env("WHATSAPP_CLOUD_TOKEN"), env("WHATSAPP_CLOUD_PHONE_NUMBER_ID"), env_list("WHATSAPP_CLOUD_TO"),
            api_version=env("WHATSAPP_CLOUD_API_VERSION", "v21.0"), template=env("WHATSAPP_CLOUD_TEMPLATE"),
            template_lang=env("WHATSAPP_CLOUD_TEMPLATE_LANG", "en_US"), tz_offset_hours=tz,
        ))
    if env("OPENWA_BASE_URL") and env("OPENWA_API_KEY") and env_list("OPENWA_CHAT_IDS"):
        out.append(OpenWANotifier(env("OPENWA_BASE_URL"), env("OPENWA_API_KEY"), env("OPENWA_SESSION", "default"),
                                  env_list("OPENWA_CHAT_IDS"), tz))
    if env("DISCORD_WEBHOOK_URL"):
        out.append(DiscordNotifier(env("DISCORD_WEBHOOK_URL"), tz))
    if env("WEBHOOK_URL"):
        out.append(WebhookNotifier(env("WEBHOOK_URL"), env("WEBHOOK_SECRET"), tz))
    return out


def build_hunter(s: Optional[Settings] = None, extra_notifiers: Optional[list] = None) -> Hunter:
    s = s or Settings.from_env()
    return Hunter(
        build_sources(s),
        build_notifiers(s) + list(extra_notifiers or []),
        store=SeenStore(s.path("seen.json")),
        fresh_max_age=s.fresh_sec,
        allow_undated=s.allow_undated,
        workers=s.workers,
        cycle_timeout=s.cycle_timeout,
        tick=s.tick,
    )
