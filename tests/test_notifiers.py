import hashlib
import hmac
import json
import time
from unittest import mock

from claude_referral_hunter import (
    CallbackNotifier,
    DiscordNotifier,
    Finding,
    OpenWANotifier,
    TelegramBotNotifier,
    WebhookNotifier,
    WhatsAppCloudNotifier,
    render,
)
from claude_referral_hunter.notifiers import split_text

F = Finding("https://claude.ai/referral/NiO-2E2LMQ", "x", "free week of <Claude> & more",
            posted_ts=time.time() - 12, author="@nbrempel", permalink="https://x.com/nbrempel/status/1")


class Resp:
    def __init__(self, status=200, text="ok", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}


def test_render_styles_escape_and_format():
    tg = render([F], "telegram_html")
    assert "<b>" in tg and "&lt;Claude&gt; &amp; more" in tg and "12s ago" in tg
    wa = render([F], "whatsapp")
    assert wa.startswith("*🎁 Claude referral link (fresh)*") and "_free week" in wa
    assert "https://claude.ai/referral/NiO-2E2LMQ" in render([F], "plain")


def test_split_text_respects_limit():
    parts = split_text("\n".join(f"line {i} " + "x" * 50 for i in range(100)), 500)
    assert len(parts) > 1 and all(len(p) <= 500 for p in parts)


def test_telegram_payload():
    with mock.patch("requests.post", return_value=Resp()) as post:
        TelegramBotNotifier("123:ABC", ["-100777", "@chan"]).send([F])
    assert post.call_count == 2
    url, kw = post.call_args_list[0].args[0], post.call_args_list[0].kwargs
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert kw["json"]["chat_id"] == "-100777" and kw["json"]["parse_mode"] == "HTML"


def test_whatsapp_cloud_text_and_template():
    with mock.patch("requests.post", return_value=Resp()) as post:
        WhatsAppCloudNotifier("TOKEN", "999", ["628123"]).send([F])
    kw = post.call_args.kwargs
    assert post.call_args.args[0] == "https://graph.facebook.com/v21.0/999/messages"
    assert kw["headers"]["Authorization"] == "Bearer TOKEN"
    assert kw["json"]["type"] == "text" and kw["json"]["to"] == "628123"
    with mock.patch("requests.post", return_value=Resp()) as post:
        WhatsAppCloudNotifier("T", "999", ["628"], template="referral_alert").send([F])
    body = post.call_args.kwargs["json"]
    assert body["type"] == "template" and body["template"]["name"] == "referral_alert"


def test_openwa_payload_jid_and_no_retry_on_500():
    n = OpenWANotifier("http://gw:2785/", "KEY", "main", ["081234567890", "123-456@g.us"])
    assert n.chat_ids == ["6281234567890@c.us", "123-456@g.us"]
    with mock.patch("requests.post", return_value=Resp(500, "err")) as post, mock.patch("time.sleep"):
        n.send([F])
    assert post.call_count == 2  # one per chat — a 500 is never retried (message may be delivered)
    assert post.call_args.args[0] == "http://gw:2785/api/sessions/main/messages/send-text"
    assert post.call_args.kwargs["headers"]["X-API-Key"] == "KEY"
    with mock.patch("requests.post", side_effect=[Resp(503), Resp(200)]) as post, mock.patch("time.sleep"):
        OpenWANotifier("http://gw", "K", "s", ["62811@c.us"]).send([F])
    assert post.call_count == 2  # 503 is retried


def test_webhook_signature_verifies():
    with mock.patch("requests.post", return_value=Resp()) as post:
        WebhookNotifier("https://bot.example/hook", secret="s3cret").send([F])
    kw = post.call_args.kwargs
    raw = kw["data"]
    expected = "sha256=" + hmac.new(b"s3cret", raw, hashlib.sha256).hexdigest()
    assert kw["headers"]["X-Signature-256"] == expected
    body = json.loads(raw)
    assert body["event"] == "referral.found" and body["findings"][0]["code"] == "NiO-2E2LMQ"


def test_discord_and_callback():
    with mock.patch("requests.post", return_value=Resp(204)) as post:
        DiscordNotifier("https://discord.com/api/webhooks/x").send([F])
    assert post.call_args.kwargs["json"]["allowed_mentions"] == {"parse": []}
    got = []
    CallbackNotifier(got.extend).send([F])
    assert got == [F]
