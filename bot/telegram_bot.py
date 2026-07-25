"""
Telegram front-end. Thin by design: this file's only jobs are (1) turn a
Telegram update into a plain-text turn for the agent, (2) keep one
long-lived ClaudeSDKClient session per chat so multi-turn bills and normal
conversation continuity work, and (3) ship back whatever text/files the
agent produced. No business logic lives here.

Idempotency: Telegram (and any webhook-based delivery in particular) can
redeliver the same update — on a retry, a timeout, or a restart mid-flight.
We dedupe on update_id in the `processed_updates` table *before* the message
ever reaches the agent, independent of whatever finalize_bill-level
idempotency the tool layer also provides. Belt and suspenders: even if a
duplicate slipped through here, finalize_bill's idempotency_key would still
catch a duplicate "finalize" call.

`/new` — start a fresh conversation for this chat: disconnects and drops the
cached ClaudeSDKClient, so the next message gets a brand new agent session
with no conversation history. Owner preferences are NOT conversation
history — they're reloaded from the DB into the system prompt of that new
session (see agent/options.py), so they survive /new. That's the whole
point of the durable-memory requirement.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env before anything below reads os.environ — TELEGRAM_BOT_TOKEN,
# ANTHROPIC_API_KEY, KIRANA_DB_PATH all come from there. Searches the
# current directory and its parents, so this works whether you run
# `python -m bot.telegram_bot` from the project root or a subdirectory.
load_dotenv()

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

from claude_agent_sdk import (
    ClaudeSDKClient, AssistantMessage, UserMessage, ResultMessage,
    TextBlock, ToolResultBlock,
)

from db.db import init_db, write_txn
from agent.options import build_options

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("kirana_bot")

# One live SDK session per Telegram chat. A fresh ClaudeSDKClient (no
# `resume`/`continue_conversation`) starts a brand-new agent session, which is
# exactly what /new should do.
_clients: dict[str, ClaudeSDKClient] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(chat_id: str) -> asyncio.Lock:
    if chat_id not in _locks:
        _locks[chat_id] = asyncio.Lock()
    return _locks[chat_id]


def _already_processed(update_id: int) -> bool:
    """Returns True (and records it) exactly once per update_id — a second
    call for the same id returns True without re-inserting, so a redelivered
    update is dropped before it ever reaches the agent."""
    with write_txn() as conn:
        try:
            conn.execute(
                "INSERT INTO processed_updates (update_id, chat_id) VALUES (?, ?)",
                (update_id, "pending"),
            )
            return False
        except Exception:
            return True


async def _get_client(chat_id: str) -> ClaudeSDKClient:
    client = _clients.get(chat_id)
    if client is None:
        options = build_options(chat_id)
        client = ClaudeSDKClient(options=options)
        await client.connect()
        _clients[chat_id] = client
    return client


async def _reset_client(chat_id: str) -> None:
    client = _clients.pop(chat_id, None)
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            logger.exception("error disconnecting client for chat %s", chat_id)


def _extract_file_paths(text: str) -> list[str]:
    """Tool results are JSON (see tools/server.py::_wrap). Any {"file_path": "...pdf|pptx"}
    that shows up in a tool result this turn gets sent to the chat as a document."""
    paths = []
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return paths
    if isinstance(obj, dict):
        fp = obj.get("file_path")
        if isinstance(fp, str) and fp.lower().endswith((".pdf", ".pptx")):
            paths.append(fp)
    return paths


async def _run_turn(chat_id: str, user_text: str) -> tuple[str, list[str]]:
    client = await _get_client(chat_id)
    await client.query(user_text)

    reply_parts: list[str] = []
    file_paths: list[str] = []

    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    reply_parts.append(block.text.strip())
        elif isinstance(message, UserMessage):
            content = message.content if isinstance(message.content, list) else []
            for block in content:
                if isinstance(block, ToolResultBlock) and block.content:
                    blocks = block.content if isinstance(block.content, list) else [{"type": "text", "text": block.content}]
                    for c in blocks:
                        text = c.get("text", "") if isinstance(c, dict) else str(c)
                        file_paths.extend(_extract_file_paths(text))
        elif isinstance(message, ResultMessage):
            if message.is_error:
                logger.warning("agent turn ended with error for chat %s: %s", chat_id, message.result)

    reply = "\n\n".join(reply_parts) if reply_parts else "(no response)"
    return reply, file_paths


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.update_id is not None and _already_processed(update.update_id):
        logger.info("dropping redelivered update_id=%s", update.update_id)
        return

    chat_id = str(update.effective_chat.id)
    text = update.message.text if update.message else None
    if not text:
        return

    async with _lock_for(chat_id):
        try:
            reply, file_paths = await _run_turn(chat_id, text)
        except Exception as e:
            logger.exception("agent turn failed for chat %s", chat_id)
            await context.bot.send_message(chat_id=chat_id, text=f"Something went wrong on my end: {e}")
            return

        if reply:
            await context.bot.send_message(chat_id=chat_id, text=reply)
        for path in file_paths:
            p = Path(path)
            if p.exists():
                await context.bot.send_document(chat_id=chat_id, document=p.open("rb"), filename=p.name)


async def handle_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    await _reset_client(chat_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text="Started a fresh conversation. Your standing preferences (payment default, "
             "brand defaults, shop details) still apply.",
    )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    await context.bot.send_message(
        chat_id=chat_id,
        text="Supermarket Ops Agent ready. Talk to me the way you'd talk to a clerk — "
             "\"50 packets of Maggi came in, cost 12, mrp 14\", \"make a bill: 2kg sugar, "
             "1 atta, UPI\", \"Ramesh's balance?\", \"close the day\". Say /new for a fresh "
             "conversation (your preferences carry over).",
    )


def build_application() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
    init_db()
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("new", handle_new))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def main() -> None:
    app = build_application()
    logger.info("Supermarket Ops Agent starting (polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
