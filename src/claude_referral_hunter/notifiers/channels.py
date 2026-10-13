"""Ready-made notifiers: console, Telegram bot, WhatsApp Cloud API (official),
OpenWA (unofficial WhatsApp), Discord, generic webhook, Python callback."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Callable, Optional, Sequence

import requests

from ..models import Finding
from .base import render, split_text

logger = logging.getLogger("claude_referral_hunter")
TIMEOUT = 20


def _post(url: str, *, retry_statuses: tuple = (429, 502, 503, 504), attempts: int = 3, **kw) -> requests.Response:
    """POST with a small retry on transient gateway errors only.

    HTTP 500 is NOT retried: several WhatsApp gateways (OpenWA) answer 500 after
    the message was actually delivered, so a retry would send it twice.
    """
    resp = None
    for attempt in range(1, attempts + 1):
        resp = requests.post(url, timeout=TIMEOUT, **kw)
        if resp.status_code not in retry_statuses or attempt == attempts:
            return resp
        wait = float(resp.headers.get("Retry-After") or attempt * 2)
        time.sleep(min(wait, 30))
    return resp


class ConsoleNotifier:
    name = "console"

    def __init__(self, tz_offset_hours: float = 0) -> None:
        self.tz = tz_offset_hours

    def send(self, findings: Sequence[Finding]) -> None:
        print(render(findings, "plain", tz_offset_hours=self.tz), flush=True)


class TelegramBotNotifier:
    """Telegram Bot API (create a bot with @BotFather). chat_ids: users, groups or @channels."""

    name = "telegram"

    def __init__(self, token: str, chat_ids: Sequence[str], tz_offset_hours: float = 0,
                 api_base: str = "https://api.telegram.org") -> None:
        self.url = f"{api_base.rstrip('/')}/bot{token}/sendMessage"
        self.chat_ids, self.tz = list(chat_ids), tz_offset_hours

    def send(self, findings: Sequence[Finding]) -> None:
        text = render(findings, "telegram_html", tz_offset_hours=self.tz)
        for chat in self.chat_ids:
            for part in split_text(text, 4000):
                resp = _post(self.url, json={"chat_id": chat, "text": part, "parse_mode": "HTML",
                                             "disable_web_page_preview": False})
                if resp.status_code != 200:
                    logger.error("telegram → %s: HTTP %s %s", chat, resp.status_code, resp.text[:200])


class WhatsAppCloudNotifier:
    """Official WhatsApp Business Cloud API (Meta).

    Free-form text is only delivered inside a 24 h customer-service window (the
    recipient messaged your business number recently). Outside it, Meta requires
    an approved template — set ``template`` to a template with one body
    parameter and the message text is passed as that parameter.
    """

    name = "whatsapp_cloud"

    def __init__(self, token: str, phone_number_id: str, recipients: Sequence[str], *,
                 api_version: str = "v21.0", template: str = "", template_lang: str = "en_US",
                 tz_offset_hours: float = 0) -> None:
        self.url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.recipients, self.template, self.template_lang = list(recipients), template, template_lang
        self.tz = tz_offset_hours

    def _payload(self, to: str, text: str) -> dict:
        if self.template:
            return {"messaging_product": "whatsapp", "to": to, "type": "template",
                    "template": {"name": self.template, "language": {"code": self.template_lang},
                                 "components": [{"type": "body", "parameters": [{"type": "text", "text": text}]}]}}
        return {"messaging_product": "whatsapp", "to": to, "type": "text",
                "text": {"body": text, "preview_url": True}}

    def send(self, findings: Sequence[Finding]) -> None:
        text = render(findings, "whatsapp", tz_offset_hours=self.tz)
        limit = 1000 if self.template else 4000
        for to in self.recipients:
            for part in split_text(text, limit):
                resp = _post(self.url, headers=self.headers, json=self._payload(to, part))
                if resp.status_code not in (200, 201):
                    logger.error("whatsapp_cloud → %s: HTTP %s %s", to, resp.status_code, resp.text[:300])


class OpenWANotifier:
    """Unofficial WhatsApp through an OpenWA gateway (WhatsApp Web session).

    ``POST {base}/api/sessions/{session}/messages/send-text`` with ``X-API-Key``.
    chat ids look like ``6281234567890@c.us`` (person) or ``...@g.us`` (group).
    """

    name = "openwa"

    def __init__(self, base_url: str, api_key: str, session: str, chat_ids: Sequence[str],
                 tz_offset_hours: float = 0) -> None:
        self.url = f"{base_url.rstrip('/')}/api/sessions/{session}/messages/send-text"
        self.headers = {"Content-Type": "application/json", "X-API-Key": api_key}
        self.chat_ids, self.tz = [self._jid(c) for c in chat_ids], tz_offset_hours

    @staticmethod
    def _jid(chat: str) -> str:
        chat = chat.strip()
        if "@" in chat:
            return chat
        digits = "".join(ch for ch in chat if ch.isdigit())
        if digits.startswith("0"):
            digits = "62" + digits[1:]  # Indonesian local format 08… → 628…
        return f"{digits}@c.us"

    def send(self, findings: Sequence[Finding]) -> None:
        text = render(findings, "whatsapp", tz_offset_hours=self.tz)
        for chat in self.chat_ids:
            for part in split_text(text, 3500):
                resp = _post(self.url, headers=self.headers, json={"chatId": chat, "text": part})
                if resp.status_code >= 400:
                    logger.error("openwa → %s: HTTP %s %s", chat, resp.status_code, resp.text[:200])


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url: str, tz_offset_hours: float = 0) -> None:
        self.url, self.tz = webhook_url, tz_offset_hours

    def send(self, findings: Sequence[Finding]) -> None:
        for part in split_text(render(findings, "markdown", tz_offset_hours=self.tz), 1900):
            resp = _post(self.url, json={"content": part, "allowed_mentions": {"parse": []}})
            if resp.status_code >= 400:
                logger.error("discord: HTTP %s %s", resp.status_code, resp.text[:200])


class WebhookNotifier:
    """POST JSON ``{"event": "referral.found", "findings": [...], "text": "..."}``.

    With a secret, the body is signed: header ``X-Signature-256: sha256=<hex>``
    (HMAC-SHA256 of the raw body), so your bot can verify the sender. Use this to
    plug the hunter into any bot framework, n8n, Make, Zapier, etc.
    """

    name = "webhook"

    def __init__(self, url: str, secret: str = "", tz_offset_hours: float = 0) -> None:
        self.url, self.secret, self.tz = url, secret, tz_offset_hours

    def body(self, findings: Sequence[Finding]) -> bytes:
        payload = {
            "event": "referral.found",
            "sent_at": int(time.time()),
            "findings": [f.to_dict() for f in findings],
            "text": render(findings, "plain", tz_offset_hours=self.tz),
        }
        return json.dumps(payload, ensure_ascii=False).encode()

    def send(self, findings: Sequence[Finding]) -> None:
        raw = self.body(findings)
        headers = {"Content-Type": "application/json"}
        if self.secret:
            sig = hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()
            headers["X-Signature-256"] = f"sha256={sig}"
        resp = _post(self.url, headers=headers, data=raw)
        if resp.status_code >= 400:
            logger.error("webhook: HTTP %s %s", resp.status_code, resp.text[:200])


class CallbackNotifier:
    """Call your own Python function — the simplest way to embed in an existing bot."""

    def __init__(self, func: Callable[[Sequence[Finding]], None], name: Optional[str] = None) -> None:
        self.func, self.name = func, name or getattr(func, "__name__", "callback")

    def send(self, findings: Sequence[Finding]) -> None:
        self.func(findings)
