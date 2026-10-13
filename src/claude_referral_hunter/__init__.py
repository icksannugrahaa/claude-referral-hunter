"""claude-referral-hunter — find fresh Claude referral links, push them to any bot."""

__version__ = "0.1.0"

from .hunter import Hunter  # noqa: E402
from .models import Finding, extract_links, referral_key  # noqa: E402
from .notifiers import (  # noqa: E402
    CallbackNotifier,
    ConsoleNotifier,
    DiscordNotifier,
    OpenWANotifier,
    TelegramBotNotifier,
    WebhookNotifier,
    WhatsAppCloudNotifier,
    render,
)
from .sources import PUBLIC_SOURCES, RedditOAuth, Source, TelegramRelay, ThreadsSearch, XSearch  # noqa: E402
from .store import SeenStore  # noqa: E402

__all__ = [
    "__version__", "Hunter", "Finding", "extract_links", "referral_key", "SeenStore", "Source",
    "PUBLIC_SOURCES", "TelegramRelay", "XSearch", "RedditOAuth", "ThreadsSearch", "render",
    "CallbackNotifier", "ConsoleNotifier", "DiscordNotifier", "OpenWANotifier", "TelegramBotNotifier",
    "WebhookNotifier", "WhatsAppCloudNotifier",
]
