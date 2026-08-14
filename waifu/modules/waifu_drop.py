"""
waifu/modules/waifu_drop.py
Final milestone drop system + anti-spam
Auto timed drops disabled.
"""

from html import escape
import random
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler, MessageHandler, filters

from waifu import (
    application, collection, group_user_totals_collection,
    top_global_groups_collection, user_collection, LOGGER,
)

# ── Per-chat state ──────────────────────────────────────────────────────────
_active_char = {}
_claimed = {}
_msg_counts = {}
_last_user = {}
_warned = {}

_XP_PER_GUESS = 50

# Message milestones
_MILESTONES = {
    100: "⚪ Common",
    200: "🟡 Legendary",
    300: "🟡 Legendary",
    400: "🟣 Rare",
    500: "🟣 Rare",
    600: "🪁 Skyrise",
    700: "💮 Exclusive",
    800: "🫧 Special",
    1000: "🔮 Mythical",
    1500: "🌤️ Summer",
}


async def _send_drop(chat_id: int, bot, rarity: str | None = None) -> None:
    query = {}
    if rarity:
        query = {"rarity": rarity}

    chars = await collection.find(query).to_list(length=5000)
    if not chars:
        LOGGER.warning("No characters found for rarity %s", rarity)
        return

    char = random.choice(chars)

    _active_char[chat_id] = char
    _claimed.pop(chat_id, None)

    await bot.send_photo(
        chat_id=chat_id,
        photo=char["img_url"],
        caption=(
            f"✨ <b>A new character appeared!</b>\n"
            f"💎 {char.get('rarity', '?')}\n\n"
            f"<i>Use /guess [name] to add them to your harem!</i>"
        ),
        parse_mode=ParseMode.HTML,
    )


# ── Message counter + anti-spam ────────────────────────────────────────────
async def message_counter(update: Update, context: CallbackContext) -> None:
    if not update.effective_chat or update.effective_chat.type == "private":
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    last = _last_user.get(chat_id)

    # Consecutive spam protection
    if last and last["user_id"] == user_id:
        last["count"] += 1

        if last["count"] >= 10:
            warned_at = _warned.get(user_id, 0)

            # Ignore for 10 minutes after warning
            if time.time() - warned_at < 600:
                return

            _warned[user_id] = time.time()

            await update.message.reply_text(
                f"⚠️ {escape(update.effective_user.first_name)}, stop spamming!\n"
                f"Your messages are ignored for 10 minutes."
            )
            return
    else:
        _last_user[chat_id] = {"user_id": user_id, "count": 1}

    # Count only first 3 consecutive messages from same user
    if not last or last["user_id"] != user_id:
        _msg_counts[chat_id] = _msg_counts.get(chat_id, 0) + 1
    else:
        if last["count"] <= 3:
            _msg_counts[chat_id] = _msg_counts.get(chat_id, 0) + 1

    count = _msg_counts[chat_id]

    rarity = _MILESTONES.get(count)
    if rarity:
        await _send_drop(chat_id, context.bot, rarity)

        # Summer reached → reset counter
        if count == 1500:
            _msg_counts[chat_id] = 0


# ── /guess ─────────────────────────────────────────────────────────────────
async def guess(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    char = _active_char.get(chat_id)
    if not char:
        return

    if chat_id in _claimed:
        await update.message.reply_text(
            "❌ Already claimed by someone else! Wait for the next character."
        )
        return

    user_guess = " ".join(context.args).strip().lower() if context.args else ""
    if not user_guess:
        await update.message.reply_text("Usage: /guess <character name>")
        return

    name_parts = char["name"].lower().split()

    correct = (
        sorted(name_parts) == sorted(user_guess.split())
        or any(part == user_guess for part in name_parts)
    )

    if not correct:
        await update.message.reply_text("❌ Wrong name, try again!")
        return

    _claimed[chat_id] = user_id
    _active_char.pop(chat_id, None)

    u = update.effective_user

    await user_collection.update_one(
        {"id": user_id},
        {
            "$push": {"characters": char},
            "$inc": {"total_guesses": 1, "xp": _XP_PER_GUESS},
            "$set": {"username": u.username, "first_name": u.first_name},
            "$setOnInsert": {
                "coins": 0,
                "wins": 0,
                "favorites": [],
            },
        },
        upsert=True,
    )

    await group_user_totals_collection.update_one(
        {"user_id": user_id, "group_id": chat_id},
        {
            "$set": {"username": u.username, "first_name": u.first_name},
            "$inc": {"count": 1},
        },
        upsert=True,
    )

    await top_global_groups_collection.update_one(
        {"group_id": chat_id},
        {
            "$set": {"group_name": update.effective_chat.title},
            "$inc": {"count": 1},
        },
        upsert=True,
    )

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📖 My Harem",
            switch_inline_query_current_chat=f"collection.{user_id}"
        )
    ]])

    await update.message.reply_text(
        f'🎉 <a href="tg://user?id={user_id}">{escape(u.first_name)}</a> guessed it!\n\n'
        f'🌸 <b>{escape(char["name"])}</b>\n'
        f'📺 {escape(char["anime"])}\n'
        f'💎 {char["rarity"]}\n\n'
        f'Added to your harem! +{_XP_PER_GUESS} XP ✨',
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


# ── /fav ───────────────────────────────────────────────────────────────────
async def fav(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("Usage: /fav <character_id>")
        return

    char_id = context.args[0]

    user_doc = await user_collection.find_one({"id": user_id})

    if not user_doc:
        await update.message.reply_text("You haven't guessed any characters yet.")
        return

    char = next(
        (c for c in user_doc.get("characters", []) if c["id"] == char_id),
        None,
    )

    if not char:
        await update.message.reply_text("That character isn't in your collection.")
        return

    await user_collection.update_one(
        {"id": user_id},
        {"$set": {"favorites": [char_id]}},
    )

    await update.message.reply_text(
        f"⭐ <b>{escape(char['name'])}</b> set as your favourite!",
        parse_mode=ParseMode.HTML,
    )


# ── Register handlers ──────────────────────────────────────────────────────
application.add_handler(CommandHandler(
    ["guess", "protecc", "collect", "grab", "hunt"],
    guess,
    block=False,
))

application.add_handler(CommandHandler("fav", fav, block=False))

application.add_handler(MessageHandler(
    filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
    message_counter,
    block=False,
))
  
