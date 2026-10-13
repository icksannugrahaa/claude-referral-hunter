# claude-referral-hunter

Find **fresh Claude referral links** (`claude.ai/referral/...`) the moment they are shared, and push them to **any bot**: Telegram, WhatsApp (official Cloud API or unofficial OpenWA), Discord, or your own webhook. Use it as a ready-to-run service (CLI / Docker) or embed it as a Python library in an existing bot.

```
X · Telegram relay · Reddit · Threads · HN · GitHub · Mastodon · Lemmy · 4chan …
                         │  dedup by referral code, freshness window
                         ▼
        Telegram bot · WhatsApp Cloud · OpenWA · Discord · Webhook · your callback
```

- **Real-time where it matters.** Optional credentialed sources (X search, a Telegram relay, Reddit OAuth, Threads API) catch links within seconds. Anonymous sources work with zero setup.
- **No duplicates.** Links are deduplicated by referral *code*, so `…/AbC?s=android` and `…/AbC` count once. A link first seen too late (stale) can still be sent when it shows up fresh elsewhere.
- **Robust loop.** Sources run in parallel. A source stuck on the network never freezes the others. Failing sources back off and retry on their own.
- **Plug into anything.** Every channel is turned on by setting its environment variables. A signed webhook covers frameworks that aren't built in.

> **Use responsibly.** This tool only reads public posts and the chats you already follow. The X source logs in with an account's cookies, which is against X's Terms of Service and can get that account suspended — use a spare account, or leave it off. Respect each platform's rate limits and terms.

---

## Quick start

```bash
git clone https://github.com/icksannugrahaa/claude-referral-hunter.git
cd claude-referral-hunter
python -m venv .venv && . .venv/bin/activate
pip install -e ".[telegram]"          # core + Telegram relay source
pip install -r requirements-x.txt     # optional: X/Twitter source
cp .env.example .env                  # fill in the channels you want
claude-referral-hunter sources        # what is enabled
claude-referral-hunter test-notify    # send a test message everywhere
claude-referral-hunter run            # start hunting
```

### Docker

```bash
cp .env.example .env    # edit it
docker compose up -d --build
docker compose logs -f
```

State (dedup store, Telegram session, X cookies) lives in `./state`, mounted at `/data`.

## Commands

| Command | What it does |
|---|---|
| `run` | Run forever. Starts with a warm-up that records existing links without sending them, so a restart doesn't spam. `--no-warmup` skips it. |
| `once [--notify] [--all]` | One scan. Prints new findings as JSON lines. `--all` ignores the freshness window. |
| `probe` | Fetch every source once and print counts. Writes no state and sends nothing. Good for checking credentials. |
| `sources` | List enabled sources and notifiers. |
| `test-notify` | Send a sample message through every configured notifier. |
| `telegram-login` | Log in the Telegram *user* account used by the `tg_relay` source. |

## Notifiers (where links are sent)

Each notifier turns on automatically when its variables are set in `.env`. Several can run at once.

### Telegram bot
1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Send your bot a message (or add it to a group/channel as admin).
3. `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_IDS=123456789,-1001234567890,@mychannel
   ```

### WhatsApp — official Cloud API (Meta)
1. Create an app at <https://developers.facebook.com> → WhatsApp → API Setup. Copy the **access token** and the **phone number ID**.
2. `.env`:
   ```
   WHATSAPP_CLOUD_TOKEN=EAAG...
   WHATSAPP_CLOUD_PHONE_NUMBER_ID=1234567890
   WHATSAPP_CLOUD_TO=6281234567890
   ```
3. Free-form text is only delivered inside the **24-hour customer-service window** (the recipient messaged your business number recently). For alerts outside it, create an approved template with **one body parameter** (e.g. `Referral alert: {{1}}`) and set `WHATSAPP_CLOUD_TEMPLATE=<template_name>`.

### WhatsApp — unofficial via OpenWA
For a self-hosted [OpenWA](https://github.com/rmyndharis/OpenWA) gateway (WhatsApp Web session):
```
OPENWA_BASE_URL=http://127.0.0.1:2785
OPENWA_API_KEY=your-gateway-key
OPENWA_SESSION=default
OPENWA_CHAT_IDS=6281234567890,120363000000000000@g.us
```
It calls `POST {base}/api/sessions/{session}/messages/send-text`. Numbers like `0812…` or `62812…` become `62812…@c.us`. HTTP 500 is **not** retried, because OpenWA often returns 500 after the message was already sent.

### Discord
`DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...` (channel settings → Integrations → Webhooks).

### Generic webhook (any other bot)
```
WEBHOOK_URL=https://your-bot.example/referral
WEBHOOK_SECRET=change-me
```
Each delivery is a JSON `POST`:
```json
{
  "event": "referral.found",
  "sent_at": 1791820000,
  "findings": [{"url": "https://claude.ai/referral/AbC", "code": "AbC", "source": "x",
                "author": "@someone", "posted_ts": 1791819990, "age_sec": 10,
                "context": "…", "permalink": "https://x.com/someone/status/1"}],
  "text": "🎁 Claude referral link (fresh) …"
}
```
When a secret is set, the body is signed with `X-Signature-256: sha256=<hmac>`. Verify it like this:
```python
import hmac, hashlib
expected = "sha256=" + hmac.new(SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-Signature-256"])
```
This is the easiest way to connect bots in Node.js, Go, PHP, n8n, Make, Zapier, and similar tools.

## Sources (where links are found)

| Source | Setup | Speed | Notes |
|---|---|---|---|
| `tg_relay` | Telegram user account | seconds | Reads chats/bots/channels that already post links (e.g. another notifier bot). |
| `x` | spare X account cookies | ~30 s | Most links are shared on X. Against X's ToS, see below. |
| `reddit` | Reddit "script" app | ~20 s | Site-wide search plus new posts and comments. Replaces `reddit_rss` when set. |
| `threads` | Threads API token | ~60 s | Needs Meta **App Review** for `threads_keyword_search`; before that it only returns your own posts. |
| `hackernews`, `github`, `mastodon`, `lemmy`, `telegram_public`, `lobsters`, `fourchan`, `reddit_rss` | none | 30 s–5 min | Free, low hit-rate. `reddit_rss` gets HTTP 429 quickly. |

Choose the anonymous sources with `CRH_SOURCES=hackernews,github,...`. Override any interval with `CRH_INTERVAL_<NAME>=seconds`.

### Telegram relay
1. Get `api_id` / `api_hash` at <https://my.telegram.org> → *API development tools*, and set `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`.
2. Install the extra with `pip install -e ".[telegram]"`, then run `claude-referral-hunter telegram-login`. It asks for your phone number, the login code, and your 2FA password if set. At the end it lists your chats.
3. Set `TG_RELAY_CHATS=<username or id>` for the chats to read.

The session file `state/telegram.session` gives **full access to your Telegram account**. Keep it private, and revoke it any time in Telegram → Settings → Devices.

### X / Twitter
1. Log in to x.com with a **spare** account.
2. In DevTools → Application → Cookies, copy `auth_token` and `ct0` into `state/x_cookies.json`:
   ```json
   {"auth_token": "...", "ct0": "..."}
   ```
3. Install with `pip install -r requirements-x.txt`. This is a patched twikit fork pinned to a reviewed commit, because upstream 2.3.3 broke in March 2026.

X rotates its internal GraphQL ids. When the log says `SearchTimeline query id expired (404)`:
1. Open `https://x.com/search?q=claude.ai%2Freferral&f=live` while logged in.
2. In DevTools → Network, filter `SearchTimeline` and copy the id from `…/graphql/<ID>/SearchTimeline`.
3. Set `X_SEARCH_QID=<ID>`.

If you then get `400 from X`, copy that request's `features` parameter into `X_SEARCH_FEATURES` as JSON.

### Reddit OAuth
Create a **script** app at <https://www.reddit.com/prefs/apps>, then set `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` and a descriptive `REDDIT_USER_AGENT`.

## Use it as a library

Embed it in a bot you already run:

```python
from claude_referral_hunter import Hunter, SeenStore, Source, PUBLIC_SOURCES, CallbackNotifier

def on_links(findings):
    for f in findings:
        my_bot.send_message(ADMIN_CHAT, f"New Claude referral: {f.url} ({f.source})")

fn, interval = PUBLIC_SOURCES["hackernews"]
hunter = Hunter(
    sources=[Source("hackernews", fn, interval)],
    notifiers=[CallbackNotifier(on_links)],
    store=SeenStore("state/seen.json"),
    fresh_max_age=600,
)
hunter.run_forever()          # blocking — run it in a thread next to your bot
```

To use your `.env` setup and add your own callback on top:

```python
import threading
from claude_referral_hunter import CallbackNotifier
from claude_referral_hunter.config import build_hunter

hunter = build_hunter(extra_notifiers=[CallbackNotifier(on_links)])
threading.Thread(target=hunter.run_forever, daemon=True).start()
```

Any object with a `name` attribute and a `send(findings)` method works as a notifier. A source is just `Source(name, fetch_function, interval_seconds)`. See [`examples/`](examples/) for a python-telegram-bot integration and a webhook receiver.

## Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `CRH_STATE_DIR` | `state` | Dedup store, Telegram session, X cookies |
| `CRH_FRESH_SEC` | `600` | Only send links posted within this many seconds |
| `CRH_ALLOW_UNDATED` | `0` | Also send links whose post time is unknown |
| `CRH_SOURCES` | all anonymous | Comma list of anonymous sources |
| `CRH_INTERVAL_<NAME>` | per source | Poll interval in seconds |
| `CRH_CYCLE_TIMEOUT` | `60` | Max seconds a cycle waits for slow sources |
| `CRH_WORKERS` | `6` | Parallel fetches |
| `CRH_TZ_OFFSET` | `0` | Hours offset for timestamps in messages (`7` = WIB) |
| `CRH_CONSOLE` | `1` | Also print findings to stdout |

Every channel and credential variable is listed in [`.env.example`](.env.example).

## Running 24/7

- **Docker:** `restart: unless-stopped` is already set in `docker-compose.yml`.
- **systemd / PM2:** run `claude-referral-hunter run` from the project directory, where `.env` lives, or point `CRH_ENV_FILE` at it.
- **Android (Termux):** hold a wake lock (`termux-wake-lock`). Otherwise the CPU sleeps with the screen off and links posted at night are missed.

## Bahasa Indonesia (ringkas)

Bot ini mencari link referral Claude yang **baru saja dibagikan**, lalu mengirimkannya ke **Telegram, WhatsApp (Cloud API resmi atau OpenWA), Discord, atau webhook** Anda.

Cara pakai:
1. Salin `.env.example` ke `.env`.
2. Isi channel yang Anda pakai.
3. Jalankan `claude-referral-hunter test-notify` untuk mengirim pesan uji.
4. Jalankan `claude-referral-hunter run` untuk mulai berburu.

Sumber tercepat adalah **Telegram relay** (membaca bot/channel lain yang sudah menyiarkan link) dan **X**. Untuk X, pakai akun cadangan karena berisiko dibekukan. Kalau dijalankan di HP Android lewat Termux, aktifkan `termux-wake-lock`.

## License

MIT
