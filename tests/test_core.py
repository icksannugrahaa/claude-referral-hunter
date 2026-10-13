import threading
import time

from claude_referral_hunter import Finding, Hunter, SeenStore, Source, extract_links, referral_key
from claude_referral_hunter.models import parse_ts
from claude_referral_hunter.store import NOTIFIED, STALE


def test_extract_and_key_normalise_variants():
    text = "go https://claude.ai/referral/hQ60KLyPow?s=android, or http://CLAUDE.ai/referral/hQ60KLyPow."
    assert extract_links(text) == ["https://claude.ai/referral/hQ60KLyPow", "http://CLAUDE.ai/referral/hQ60KLyPow"]
    a, b = (Finding(u, "t") for u in extract_links(text))
    assert a.key == b.key and a.url == b.url == "https://claude.ai/referral/hQ60KLyPow"
    assert Finding("https://claude.ai/referral/hq60klypow", "t").key != a.key  # case-sensitive codes
    assert referral_key("https://claude.com/invite/AbC") == "invite/AbC"


def test_parse_ts_formats():
    assert parse_ts(1700000000) == 1700000000.0
    assert parse_ts("2026-10-12T14:00:00Z") == parse_ts("2026-10-12T14:00:00+0000")
    assert parse_ts("Mon, 12 Oct 2026 14:00:00 GMT") == parse_ts("2026-10-12T14:00:00Z")
    assert parse_ts("garbage") is None and parse_ts(None) is None


def _hunter(sources, store=None, **kw):
    sent = []
    h = Hunter(sources, [], store=store if store is not None else SeenStore(None), on_findings=sent.extend, **kw)
    return h, sent


def test_fresh_dedup_and_warmup():
    now = time.time()
    links = [Finding("https://claude.ai/referral/A1", "s", posted_ts=now),
             Finding("https://claude.ai/referral/A1?s=android", "s2", posted_ts=now),
             Finding("https://claude.ai/referral/Old", "s", posted_ts=now - 3600)]
    h, sent = _hunter([Source("s", lambda: links)], fresh_max_age=600)
    assert [f.code for f in h.scan()] == ["A1"]
    assert h.scan() == []  # notified once
    assert [f.code for f in sent] == ["A1"]

    h2, sent2 = _hunter([Source("s", lambda: links)])
    assert len(h2.warm_up() and h2.store) == 2  # warm-up records, sends nothing
    assert sent2 == [] and h2.scan() == []


def test_stale_sighting_does_not_block_later_fresh_one():
    """X saw the code too late (stale), then a relay bot posted it fresh."""
    store = SeenStore(None)
    late = [Finding("https://claude.ai/referral/dr1ENVUdcQ", "x", posted_ts=time.time() - 700)]
    h, sent = _hunter([Source("x", lambda: late)], store=store, fresh_max_age=600)
    assert h.scan() == [] and h.scan() == []
    assert store.get(late[0])["state"] == STALE
    relay = [Finding("https://claude.ai/referral/dr1ENVUdcQ", "tg_relay:bot", posted_ts=time.time())]
    h.sources = [Source("tg_relay", lambda: relay)]
    assert [f.code for f in h.scan()] == ["dr1ENVUdcQ"]
    assert h.scan() == []
    assert store.get(late[0])["state"] == NOTIFIED and len(sent) == 1


def test_hung_source_does_not_freeze_cycle_and_is_collected_later():
    release = threading.Event()

    def hung():
        release.wait(5)
        return [Finding("https://claude.ai/referral/Late", "hung", posted_ts=time.time())]

    fast = lambda: [Finding("https://claude.ai/referral/Fast", "fast", posted_ts=time.time())]
    h, sent = _hunter([Source("hung", hung), Source("fast", fast)], cycle_timeout=0.3)
    started = time.time()
    assert [f.code for f in h.scan()] == ["Fast"]
    assert time.time() - started < 1.5
    assert h.scan() == []  # hung still running: not resubmitted
    release.set()
    time.sleep(0.2)
    assert [f.code for f in h.scan()] == ["Late"]


def test_broken_source_and_notifier_do_not_stop_others(caplog):
    class Boom:
        name = "boom"

        def send(self, findings):
            raise RuntimeError("down")

    got = []

    class Ok:
        name = "ok"

        def send(self, findings):
            got.extend(findings)

    def broken():
        raise RuntimeError("source bug")

    good = lambda: [Finding("https://claude.ai/referral/G", "g", posted_ts=time.time())]
    h = Hunter([Source("broken", broken), Source("good", good)], [Boom(), Ok()], store=SeenStore(None))
    assert [f.code for f in h.scan()] == ["G"] and [f.code for f in got] == ["G"]


def test_empty_file_store_is_kept_and_persisted(tmp_path):
    """Regression: an empty SeenStore is falsy; Hunter must not swap it for a memory store."""
    path = str(tmp_path / "seen.json")
    store = SeenStore(path)
    h = Hunter([Source("s", lambda: [Finding("https://claude.ai/referral/P1", "s", posted_ts=time.time())])],
               store=store)
    assert h.store is store
    h.scan(notify=False)
    assert SeenStore(path).blocks(Finding("https://claude.ai/referral/P1", "s"))


def test_store_persists_and_caps(tmp_path):
    path = str(tmp_path / "seen.json")
    store = SeenStore(path, max_entries=10)
    for i in range(15):
        store.mark(Finding(f"https://claude.ai/referral/C{i}", "s"), NOTIFIED)
    store.save()
    again = SeenStore(path)
    assert 0 < len(again) <= 10
    assert again.blocks(Finding("https://claude.ai/referral/C14", "s"))


def test_due_respects_intervals():
    h, _ = _hunter([Source("fast", lambda: [], 10), Source("slow", lambda: [], 300)])
    h.scan()
    assert h.due(time.time() + 20) == [h.sources[0]]
