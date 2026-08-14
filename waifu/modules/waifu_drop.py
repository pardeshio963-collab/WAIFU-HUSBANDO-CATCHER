"""
modules/waifu_drop.py

Core game loop:
  - Message counter → threshold drop
  - APScheduler timed drop every N minutes
  - /guess to claim
  - /fav to favourite
  - Anti-spam (10 consecutive messages from same user → 10-min ignore)

Bug fixes vs previous version:
  1. _active_char is cleared immediately after a correct guess so the same
     drop cannot be guessed twice. Previously it lingered until the NEXT drop.
  2. _sent_ids now uses a rolling window (capped at half the catalogue size,
     minimum 20). The old "len(sent)==len(all_chars)" reset condition was
     never triggered when new characters were added to the DB while the bot
     was running, permanently blacklisting those characters from reappearing.
  3. XP is now awarded for a correct guess (50 XP, configurable).
"""
import asyncio
import random
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import (
    application, collection, group_user_totals_collection,
    top_global_groups_collection, user_collection, user_totals_collection,
    LOGGER,
)
from waifu.config import Config

# ── Per-chat in-memory state ──────────────────────────────────────────────────
_active_char:      dict[int, dict]  = {}   # chat_id → currently active character
_claimed:          dict[int, int]   = {}   # chat_id → user_id who claimed it
_msg_counts:       dict[int, int]   = {}   # chat_id → message counter
_last_user:        dict[int, dict]  = {}   # chat_id → {user_id, count}
_warned:           dict[int, float] = {}   # user_id → timestamp of last warning
# Rolling window of recently sent char IDs per chat — capped dynamically
_sent_ids:         dict[int, list]  = {}
_registered_chats: set[int]         = set()


# XP reward for a correct guess
_XP_PER_GUESS = 50


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _chat_frequency(chat_id: int) -> int:
    doc = await user_totals_collection.find_one({"chat_id": chat_id})
    return int(doc["message_frequency"]) if doc and "message_frequency" in doc \
        else Config.DEFAULT_MSG_FREQUENCY


def _rolling_window_size(total_chars: int) -> int:
    """
    How many recently-sent IDs to remember before a character can reappear.
    Capped at half the catalogue (minimum 20) so even a 1-character DB works.
    """
    return max(20, total_chars // 2)


async def _send_drop(chat_id: int, bot) -> None:
    """Pick a random unseen-recently character and post it to the chat."""
    all_chars = await collection.find({}).to_list(length=5000)
    if not all_chars:
        LOGGER.debug("No characters in DB — skipping drop for chat %s", chat_id)
        return

    window = _rolling_window_size(len(all_chars))
    sent   = _sent_ids.get(chat_id, [])

    # Characters not in the rolling window
    unsent = [c for c in all_chars if c["id"] not in sent]

    # If every character has been seen recently, clear the window and start fresh
    if not unsent:
        _sent_ids[chat_id] = []
        unsent = all_chars
        LOGGER.debug("Sent-IDs window cleared for chat %s (all %d chars seen)",
                     chat_id, len(all_chars))

    char = random.choice(unsent)

    # Append to rolling window; trim to keep only the most recent `window` entries
    new_sent = sent + [char["id"]]
    _sent_ids[chat_id] = new_sent[-window:]

    # Register as the active drop — clear any previous claim state
    _active_char[chat_id] = char
    _claimed.pop(chat_id, None)   # ← fresh drop, anyone can claim

    try:
        await bot.send_photo(
            chat_id=chat_id,
            photo=char["img_url"],
            caption=(
                f"✨ <b>A new character appeared!</b>\n\n"
                f"<i>Use /guess [name] to add them to your harem!</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
        LOGGER.info("Drop sent to chat %s: %s (%s)",
                    chat_id, char["name"], char.get("rarity", "?"))
    except Exception as e:
        # Roll back state if we couldn't actually post the message
        _active_char.pop(chat_id, None)
        LOGGER.warning("Drop failed in chat %s: %s", chat_id, e)


# ── Scheduler disabled ───────────────────────────────────────────────────────
# Timed drops are intentionally disabled.
# Drops now happen only through the per-chat message counter and /changetime.


# ── Message counter ───────────────────────────────────────────────────────────

async def message_counter(update: Update, context: CallbackContext) -> None:
    if not update.effective_chat or update.effective_chat.type == "private":
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    _registered_chats.add(chat_id)

    # Anti-spam: 10 consecutive messages from the same user → 10-min ignore
    last = _last_user.get(chat_id)
    if last and last["user_id"] == user_id:
        last["count"] += 1
        if last["count"] >= 10:
            warned_at = _warned.get(user_id, 0)
            if time.time() - warned_at < 600:
                return   # still within the ignore window
            _warned[user_id] = time.time()
            await update.message.reply_text(
                f"⚠️ {escape(update.effective_user.first_name)}, slow down!\n"
                f"Your messages will be ignored for 10 minutes."
            )
            return
    else:
        _last_user[chat_id] = {"user_id": user_id, "count": 1}

    _msg_counts[chat_id] = _msg_counts.get(chat_id, 0) + 1
    freq = await _chat_frequency(chat_id)
    if _msg_counts[chat_id] >= freq:
        _msg_counts[chat_id] = 0
        await _send_drop(chat_id, context.bot)


# ── /guess ────────────────────────────────────────────────────────────────────

async def guess(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # No active drop in this chat
    char = _active_char.get(chat_id)
    if not char:
        return   # silent — no character is waiting

    # Already claimed in this drop session
    if chat_id in _claimed:
        await update.message.reply_text(
            "❌ Already claimed by someone else! Wait for the next character."
        )
        return

    user_guess = " ".join(context.args).strip().lower() if context.args else ""
    if not user_guess:
        await update.message.reply_text("Usage: /guess <character name>")
        return

    # Reject obviously malicious input
    if any(bad in user_guess for bad in ("()", "&&", "||", "<script")):
        await update.message.reply_text("❌ Invalid characters in guess.")
        return

    # Name matching: full name match OR any single word of the name
    name_parts = char["name"].lower().split()
    correct = (
        sorted(name_parts) == sorted(user_guess.split())
        or any(part == user_guess for part in name_parts)
    )

    if not correct:
        await update.message.reply_text("❌ Wrong name, try again!")
        return

    # ── Correct guess ─────────────────────────────────────────────────────────
    # Mark claimed AND immediately clear the active drop so no second guess
    # is possible for this session — even if another user is mid-typing.
    _claimed[chat_id]    = user_id
    _active_char.pop(chat_id, None)   # ← KEY FIX: drop is over, clear it now

    # ── Persist to user document (upsert — never insert_one to avoid dup-key) ──
    u = update.effective_user
    await user_collection.update_one(
        {"id": user_id},
        {
            "$push": {"characters": char},
            "$inc":  {"total_guesses": 1, "xp": _XP_PER_GUESS},
            "$set":  {"username": u.username, "first_name": u.first_name},
            "$setOnInsert": {
                "coins":     0,
                "wins":      0,
                "favorites": [],
            },
        },
        upsert=True,
    )

    # ── Group totals ──────────────────────────────────────────────────────────
    await group_user_totals_collection.update_one(
        {"user_id": user_id, "group_id": chat_id},
        {"$set":  {"username": u.username, "first_name": u.first_name},
         "$inc":  {"count": 1}},
        upsert=True,
    )
    await top_global_groups_collection.update_one(
        {"group_id": chat_id},
        {"$set": {"group_name": update.effective_chat.title},
         "$inc": {"count": 1}},
        upsert=True,
    )

    # ── Success reply ─────────────────────────────────────────────────────────
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📖 My Harem",
            switch_inline_query_current_chat=f"collection.{user_id}",
        )
    ]])
    await update.message.reply_text(
        f'🎉 <a href="tg://user?id={user_id}">{escape(u.first_name)}</a> '
        f'guessed it!\n\n'
        f'🌸 <b>{escape(char["name"])}</b>\n'
        f'📺 {escape(char["anime"])}\n'
        f'💎 {char["rarity"]}\n\n'
        f'Added to your harem! +{_XP_PER_GUESS} XP ✨',
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


# ── /fav ──────────────────────────────────────────────────────────────────────

async def fav(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /fav <character_id>")
        return

    char_id  = context.args[0]
    user_doc = await user_collection.find_one({"id": user_id})
    if not user_doc:
        await update.message.reply_text("You haven't guessed any characters yet.")
        return

    char = next((c for c in user_doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text("That character isn't in your collection.")
        return

    await user_collection.update_one({"id": user_id}, {"$set": {"favorites": [char_id]}})
    await update.message.reply_text(
        f"⭐ <b>{escape(char['name'])}</b> set as your favourite!",
        parse_mode=ParseMode.HTML,
    )


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler(
    ["guess", "protecc", "collect", "grab", "hunt"], guess, block=False
))
application.add_handler(CommandHandler("fav", fav, block=False))
application.add_handler(MessageHandler(
    filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
    message_counter,
    block=False,
))
