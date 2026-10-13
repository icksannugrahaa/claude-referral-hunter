"""Run the hunter inside an existing python-telegram-bot (v20+) application.

    pip install "claude-referral-hunter[telegram]" python-telegram-bot
    BOT_TOKEN=... ADMIN_CHAT_ID=... python examples/telegram_bot_embed.py

Findings arrive on a worker thread, so they are handed to the bot's event loop
with asyncio.run_coroutine_threadsafe.
"""

import asyncio
import os
import threading

from telegram.ext import ApplicationBuilder, CommandHandler

from claude_referral_hunter import CallbackNotifier, render
from claude_referral_hunter.config import build_hunter

ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])


async def start(update, context):
    await update.message.reply_text("Referral hunter is running. Fresh links will appear here.")


def main() -> None:
    app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))

    async def post_init(application):
        loop = asyncio.get_running_loop()

        def on_links(findings):
            text = render(findings, "telegram_html", tz_offset_hours=7)
            asyncio.run_coroutine_threadsafe(
                application.bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML"), loop)

        hunter = build_hunter(extra_notifiers=[CallbackNotifier(on_links)])
        threading.Thread(target=hunter.run_forever, daemon=True).start()

    app.post_init = post_init
    app.run_polling()


if __name__ == "__main__":
    main()
