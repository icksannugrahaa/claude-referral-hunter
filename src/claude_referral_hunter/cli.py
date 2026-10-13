"""Command line: claude-referral-hunter <command>."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

from . import __version__
from .config import Settings, build_hunter, build_notifiers, build_sources, env
from .models import Finding


def _setup(verbose: bool) -> Settings:
    try:
        from dotenv import load_dotenv

        load_dotenv(os.getenv("CRH_ENV_FILE", ".env"))
    except ImportError:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for noisy in ("telethon", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    s = Settings.from_env()
    os.makedirs(s.state_dir, exist_ok=True)
    return s


def cmd_run(args, s: Settings) -> int:
    hunter = build_hunter(s)
    try:
        hunter.run_forever(warm_up=not args.no_warmup)
    except KeyboardInterrupt:
        hunter.stop()
    return 0


def cmd_once(args, s: Settings) -> int:
    hunter = build_hunter(s)
    found = hunter.scan(notify=args.notify, require_fresh=not args.all)
    for f in found:
        print(json.dumps(f.to_dict(), ensure_ascii=False))
    print(f"# {len(found)} link(s)", file=sys.stderr)
    return 0


def cmd_probe(args, s: Settings) -> int:
    """Fetch every source once and print counts — no state written, nothing sent."""
    from . import http

    for src in build_sources(s):
        started = time.time()
        http.clear_error()
        try:
            items = list(src.fetch())
            err = http.last_error()
        except Exception as exc:
            items, err = [], f"{type(exc).__name__}: {exc}"
        fresh = [f for f in items if f.is_fresh(s.fresh_sec, s.allow_undated)]
        newest = min((f.age_sec() for f in items if f.posted_ts), default=None)
        newest_txt = "-" if newest is None else f"{int(newest // 60)} min"
        print(f"{src.name:16s} {time.time() - started:5.1f}s  links={len(items):3d}  fresh={len(fresh)}  "
              f"newest={newest_txt}  {err}")
    return 0


def cmd_sources(args, s: Settings) -> int:
    print("sources:  ", ", ".join(f"{src.name}/{int(src.interval)}s" for src in build_sources(s)))
    print("notifiers:", ", ".join(n.name for n in build_notifiers(s)) or "(none)")
    print(f"fresh window {int(s.fresh_sec)}s · state {os.path.abspath(s.state_dir)}")
    return 0


def cmd_test_notify(args, s: Settings) -> int:
    notifiers = build_notifiers(s)
    if not notifiers:
        print("no notifier configured", file=sys.stderr)
        return 1
    sample = Finding("https://claude.ai/referral/TEST-crh-1234", "test", "This is a test message from "
                     "claude-referral-hunter.", posted_ts=time.time() - 12, author="@example")
    rc = 0
    for n in notifiers:
        try:
            n.send([sample])
            print(f"✓ {n.name}")
        except Exception as exc:
            rc = 1
            print(f"✗ {n.name}: {exc}", file=sys.stderr)
    return rc


def cmd_telegram_login(args, s: Settings) -> int:
    api_id, api_hash = env("TELEGRAM_API_ID"), env("TELEGRAM_API_HASH")
    if not api_id.isdigit() or not api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH first (https://my.telegram.org → API development tools).")
        return 1
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print('telethon missing: pip install "claude-referral-hunter[telegram]"')
        return 1
    session = env("TG_SESSION", s.path("telegram"))
    with TelegramClient(session, int(api_id), api_hash) as client:
        me = client.get_me()
        print(f"\nLogged in as {me.first_name} (@{me.username or '-'}). Session: {session}.session")
        print("\nRecent chats — put usernames/ids in TG_RELAY_CHATS:")
        for d in client.iter_dialogs(limit=40):
            kind = "bot" if getattr(d.entity, "bot", False) else type(d.entity).__name__.lower()
            print(f"  {d.id:>16}  @{getattr(d.entity, 'username', None) or '-':<28} {kind:<8} {d.name}")
    os.chmod(session + ".session", 0o600)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="claude-referral-hunter",
                                     description="Find fresh Claude referral links and push them to your bots.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")
    p_run = sub.add_parser("run", help="run forever (default)")
    p_run.add_argument("--no-warmup", action="store_true", help="notify links that already exist at start")
    p_once = sub.add_parser("once", help="one scan; prints new findings as JSON lines")
    p_once.add_argument("--notify", action="store_true", help="also send them to the notifiers")
    p_once.add_argument("--all", action="store_true", help="include links older than the fresh window")
    sub.add_parser("probe", help="fetch each source once, print counts (no state, no messages)")
    sub.add_parser("sources", help="show enabled sources and notifiers")
    sub.add_parser("test-notify", help="send a test message through every notifier")
    sub.add_parser("telegram-login", help="log in the Telegram user account for the tg_relay source")
    args = parser.parse_args(argv)
    s = _setup(args.verbose)
    handler = {
        None: cmd_run, "run": cmd_run, "once": cmd_once, "probe": cmd_probe, "sources": cmd_sources,
        "test-notify": cmd_test_notify, "telegram-login": cmd_telegram_login,
    }[args.cmd]
    if args.cmd is None:
        args.no_warmup = False
    return handler(args, s)


if __name__ == "__main__":
    raise SystemExit(main())
