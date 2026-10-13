import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from claude_referral_hunter import cli
from claude_referral_hunter.config import Settings, build_notifiers, build_sources
from claude_referral_hunter.sources import credentialed as cred
from claude_referral_hunter.sources import public


def test_tg_message_text_entities_preview():
    ent = type("MessageEntityUrl", (), {})()
    text = "🎁 Claude referral link go https://claude.ai/referral/Zz9 now"
    ent.offset, ent.length = text.index("https"), len("https://claude.ai/referral/Zz9")
    msg = SimpleNamespace(message=text, entities=[ent, SimpleNamespace(url="https://claude.ai/referral/Hid1")],
                          media=SimpleNamespace(webpage=SimpleNamespace(url="https://claude.ai/referral/Prev")),
                          date=datetime(2026, 10, 13, 5, 41, tzinfo=timezone.utc))
    found = cred.tg_message_findings(msg, "notifierbot")
    assert sorted(f.code for f in found) == ["Hid1", "Prev", "Zz9"]
    assert all(f.source == "tg_relay:notifierbot" for f in found)


def test_x_tweet_expands_tco():
    tweet = SimpleNamespace(full_text="free week https://t.co/a", id="42",
                            urls=[{"url": "https://t.co/a", "expanded_url": "https://claude.ai/referral/AbC?s=android"}],
                            created_at_datetime=datetime.now(timezone.utc), user=SimpleNamespace(screen_name="me"))
    [f] = cred.x_tweet_findings(tweet)
    assert (f.url, f.author, f.permalink) == ("https://claude.ai/referral/AbC", "@me", "https://x.com/me/status/42")


def test_reddit_and_threads_parsers():
    data = {"data": {"children": [
        {"kind": "t3", "data": {"title": "7 days", "selftext": "https://claude.ai/referral/P1", "subreddit": "ClaudeAI",
                                "created_utc": 1791800000, "permalink": "/r/ClaudeAI/x", "author": "bob"}},
        {"kind": "t1", "data": {"body": "https://claude.ai/referral/C2", "subreddit": "claude", "created_utc": 1}},
    ]}}
    found = cred.reddit_listing_findings(data, "new")
    assert [(f.code, f.source, f.author) for f in found] == [("P1", "reddit:ClaudeAI", "u/bob"), ("C2", "reddit:claude", "")]
    [t] = cred.threads_search_findings({"data": [{"text": "https://claude.ai/referral/T3", "username": "u",
                                                   "timestamp": "2026-10-12T14:00:00+0000"}]})
    assert t.code == "T3" and t.posted_ts is not None


def test_x_backoff_on_404_and_rate_limit():
    x = cred.XSearch("/nonexistent.json", ["q"])

    class NotFound(Exception):
        pass

    with mock.patch.object(cred.RUNNER, "run", side_effect=NotFound()), mock.patch.object(x, "_fetch", mock.MagicMock()):
        assert x.fetch() == []
    assert x.backoff.until - time.time() > 1700
    assert x.fetch() == []  # held, no call


def test_hackernews_parses_hits():
    resp = mock.MagicMock()
    resp.json.return_value = {"hits": [{"comment_text": "mine https://claude.ai/referral/HN1", "created_at_i": 1791800000,
                                        "author": "pg", "objectID": "9"}]}
    with mock.patch.object(public.http, "get", return_value=resp):
        [f] = public.hackernews(["q"])
    assert (f.code, f.author, f.permalink) == ("HN1", "pg", "https://news.ycombinator.com/item?id=9")


def test_build_sources_and_notifiers_from_env(tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "OPENWA_BASE_URL", "REDDIT_CLIENT_ID", "THREADS_ACCESS_TOKEN", "WEBHOOK_URL",
              "TELEGRAM_API_ID", "WHATSAPP_CLOUD_TOKEN", "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(state_dir=str(tmp_path), public_sources=["hackernews", "reddit_rss"], console=False)
    assert [src.name for src in build_sources(s)] == ["hackernews", "reddit_rss"]
    assert build_notifiers(s) == []

    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "sec")
    monkeypatch.setenv("CRH_INTERVAL_HACKERNEWS", "45")
    (tmp_path / "x_cookies.json").write_text(json.dumps({"auth_token": "a", "ct0": "b"}))
    srcs = {src.name: src for src in build_sources(s)}
    assert set(srcs) == {"hackernews", "x", "reddit"}  # reddit_rss replaced by OAuth
    assert srcs["hackernews"].interval == 45

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "1,2")
    monkeypatch.setenv("OPENWA_BASE_URL", "http://gw")
    monkeypatch.setenv("OPENWA_API_KEY", "k")
    monkeypatch.setenv("OPENWA_CHAT_IDS", "62811")
    assert [n.name for n in build_notifiers(s)] == ["telegram", "openwa"]
    with pytest.raises(ValueError):
        build_sources(Settings(state_dir=str(tmp_path), public_sources=["nope"]))


def test_cli_sources_and_test_notify(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CRH_SOURCES", "hackernews")
    for k in ("TELEGRAM_BOT_TOKEN", "OPENWA_BASE_URL", "WEBHOOK_URL", "DISCORD_WEBHOOK_URL", "WHATSAPP_CLOUD_TOKEN",
              "REDDIT_CLIENT_ID", "THREADS_ACCESS_TOKEN", "TELEGRAM_API_ID"):
        monkeypatch.delenv(k, raising=False)
    assert cli.main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "hackernews/30s" in out and "console" in out
    assert cli.main(["test-notify"]) == 0
    assert "TEST-crh-1234" in capsys.readouterr().out
