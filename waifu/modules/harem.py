"""
modules/harem.py
Harem + /w + /wrarity + /wmode
"""

import math
from html import escape
from itertools import groupby

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, waifu_collection, sudo_users, OWNER_ID
from waifu.config import Config


_PAGE = 15

RARITY_MAP = Config.RARITY_MAP

_MEDALS = {
    "⚪ Common": "⚪",
    "🟣 Rare": "🟣",
    "🟡 Legendary": "🟡",
    "🪁 Skyrise": "🪁",
    "💮 Exclusive": "💮",
    "🔮 Mythical": "🔮",
    "🫧 Special": "🫧",
    "🌤️ Summer": "🌤️",
    "🧧 Limited": "🧧",
}


def _rarity_icon(rarity: str) -> str:
    return _MEDALS.get(rarity, "🎴")


def _is_sudo(user_id: int) -> bool:
    return user_id in sudo_users


def _rarity_from_arg(raw: str) -> str | None:
    raw = raw.strip()
    if raw.isdigit():
        return RARITY_MAP.get(int(raw))
    low = raw.casefold()
    for rarity in RARITY_MAP.values():
        if rarity.casefold() == low:
            return rarity
    for rarity in RARITY_MAP.values():
        if low in rarity.casefold():
            return rarity
    return None


async def _anime_totals(animes: list[str]) -> dict[str, int]:
    if not animes:
        return {}
    pipeline = [
        {"$match": {"anime": {"$in": animes}}},
        {"$group": {"_id": "$anime", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in waifu_collection.aggregate(pipeline)}


async def _build_page(
    user_id: int,
    page: int,
    rarity: str | None = None,
) -> tuple[str, InlineKeyboardMarkup, str | None, list[dict]]:
    """Build a user's harem page, optionally filtered by rarity."""
    user = await user_collection.find_one({"id": user_id})

    if not user or not user.get("characters"):
        return (
            "📭 Your harem is empty — go catch some characters!",
            InlineKeyboardMarkup([]),
            None,
            [],
        )

    chars = user["characters"]

    if rarity:
        chars = [c for c in chars if c.get("rarity") == rarity]

    if not chars:
        text = (
            f"📭 No characters found for {_rarity_icon(rarity or '')} "
            f"<b>{escape(rarity or 'this rarity')}</b>."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 All Rarities", callback_data=f"wmode:all:{user_id}:0")
        ]])
        return text, kb, None, []

    id_counts: dict[str, int] = {}
    for c in chars:
        id_counts[c["id"]] = id_counts.get(c["id"], 0) + 1

    unique: list[dict] = list({c["id"]: c for c in chars}.values())
    unique.sort(key=lambda x: (x.get("anime", ""), x.get("id", "")))

    total_unique = len(unique)
    total_pages = max(1, math.ceil(total_unique / _PAGE))
    page = max(0, min(page, total_pages - 1))

    page_chars = unique[page * _PAGE:(page + 1) * _PAGE]
    animes = list({c.get("anime", "") for c in page_chars if c.get("anime")})
    db_totals = await _anime_totals(animes)

    fav_id = (user.get("favorites") or [None])[0]

    mode_line = (
        f"💎 Mode: {_rarity_icon(rarity)} {escape(rarity)}"
        if rarity else "💎 Mode: All Rarities"
    )

    lines = [
        f"<b>🌸 {escape(user.get('first_name', 'User'))}'s Harem</b>",
        mode_line,
        f"📦 {total_unique} unique  |  🗂 {len(chars)} total  |  "
        f"💰 {user.get('coins', 0):,} coins",
        f"Page {page + 1}/{total_pages}\n",
    ]

    sorted_page = sorted(page_chars, key=lambda x: x.get("anime", ""))
    for anime, group_iter in groupby(sorted_page, key=lambda x: x.get("anime", "")):
        group_list = list(group_iter)
        db_total = db_totals.get(anime, "?")
        lines.append(f"\n<b>{escape(anime)}  {len(group_list)}/{db_total}</b>")

        for c in group_list:
            icon = _rarity_icon(c.get("rarity", ""))
            cnt = id_counts.get(c["id"], 1)
            dup = f" ×{cnt}" if cnt > 1 else ""
            fav = " ⭐" if c["id"] == fav_id else ""
            lines.append(
                f"  {icon} <code>{escape(str(c['id']))}</code> "
                f"{escape(c.get('name', 'Unknown'))}{dup}{fav}"
            )

    text = "\n".join(lines)

    kb: list[list] = []
    kb.append([
        InlineKeyboardButton(
            f"🔍 Browse Collection ({len(chars)})",
            switch_inline_query_current_chat=f"collection.{user_id}",
        )
    ])

    # Rarity mode button.
    kb.append([
        InlineKeyboardButton(
            "🎛️ Rarity Mode",
            callback_data=f"wmode:menu:{user_id}:{page}",
        )
    ])

    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=f"harem:{page - 1}:{user_id}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="noop",
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=f"harem:{page + 1}:{user_id}",
            )
        )
    if len(nav) > 1:
        kb.append(nav)

    markup = InlineKeyboardMarkup(kb)

    photo: str | None = None
    if fav_id:
        fav_char = next((c for c in chars if c["id"] == fav_id), None)
        photo = (fav_char or {}).get("img_url")
    if not photo and page_chars:
        photo = page_chars[0].get("img_url")

    return text, markup, photo, page_chars


async def _delete_album(context: CallbackContext, user_id: int) -> None:
    """Delete character-image messages from the previous harem page."""
    old_ids = context.user_data.get("harem_album_ids", [])
    if not old_ids:
        return
    for mid in old_ids:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=mid)
        except Exception:
            pass
    context.user_data["harem_album_ids"] = []


async def _send_character_albums(context: CallbackContext, user_id: int, chars: list[dict]) -> None:
    """Send every character image on the current page, in Telegram albums."""
    await _delete_album(context, user_id)
    media: list[InputMediaPhoto] = []

    for c in chars:
        img_url = c.get("img_url")
        if not img_url:
            continue
        rarity = c.get("rarity", "")
        edition = c.get("edition")
        icon = _rarity_icon(rarity)
        edition_line = _edition_text(edition)
        caption = (
            f"{icon} <b>{escape(str(rarity or 'Unknown'))}</b>\n"
            f"{edition_line + chr(10) if edition_line else ''}"
            f"🎴 <b>{escape(str(c.get('name', 'Unknown')))}</b>\n"
            f"📺 {escape(str(c.get('anime', 'Unknown')))}"
        )
        media.append(InputMediaPhoto(media=img_url, caption=caption, parse_mode=ParseMode.HTML))

    sent_ids: list[int] = []
    # Telegram allows at most 10 items per media group.
    for i in range(0, len(media), 10):
        try:
            messages = await context.bot.send_media_group(chat_id=user_id, media=media[i:i+10])
            sent_ids.extend(m.message_id for m in messages)
        except Exception:
            # If a URL is invalid, continue with the remaining characters instead
            # of making the whole Harem fail.
            for item in media[i:i+10]:
                try:
                    msg = await context.bot.send_photo(
                        chat_id=user_id, photo=item.media, caption=item.caption, parse_mode=ParseMode.HTML
                    )
                    sent_ids.append(msg.message_id)
                except Exception:
                    pass

    context.user_data["harem_album_ids"] = sent_ids


async def _reply_harem(
    update: Update,
    context: CallbackContext,
    text: str,
    markup: InlineKeyboardMarkup,
    photo: str | None,
) -> None:
    if update.message:
        if photo:
            await update.message.reply_photo(
                photo=photo,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
        return

    q = update.callback_query
    try:
        if photo:
            await q.edit_message_media(
                media=InputMediaPhoto(
                    media=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=markup,
            )
        else:
            await q.edit_message_caption(
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def harem(update: Update, context: CallbackContext, page: int = 0) -> None:
    user_id = update.effective_user.id
    rarity = context.user_data.get("harem_rarity")
    text, markup, photo, _ = await _build_page(user_id, page, rarity)
    await _reply_harem(update, context, text, markup, photo)


async def harem_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()

    _, page_str, uid_str = q.data.split(":")
    if q.from_user.id != int(uid_str):
        await q.answer("❌ That's not your harem!", show_alert=True)
        return

    await harem(update, context, page=int(page_str))


async def noop(update: Update, context: CallbackContext) -> None:
    await update.callback_query.answer()


# ─────────────────────────────────────────────────────────────────────────────
# /w <character_id> — available to everyone
# ─────────────────────────────────────────────────────────────────────────────

async def w_character(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /w <character_id>")
        return

    char_id = context.args[0].strip()
    char = await waifu_collection.find_one({"id": char_id})

    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> not found.",
            parse_mode=ParseMode.HTML,
        )
        return

    name = escape(str(char.get("name", "Unknown")))
    anime = escape(str(char.get("anime", "Unknown")))
    rarity = escape(str(char.get("rarity", "Unknown")))
    edition = char.get("edition")
    char_id_safe = escape(str(char.get("id", char_id)))
    img = char.get("img_url")

    lines = [
        f"🌸 <b>{name}</b>",
        f"📺 <b>Anime:</b> {anime}",
        f"💎 <b>Rarity:</b> {rarity}",
    ]

    if edition:
        lines.append(f"🎀 <b>Edition:</b> {escape(str(edition))}")

    lines.append(f"🆔 <b>ID:</b> <code>{char_id_safe}</code>")
    caption = "\n".join(lines)

    if img:
        await update.message.reply_photo(
            photo=img,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────────────────────────────────────────
# /wrarity — SUDO ONLY, counts only
# ─────────────────────────────────────────────────────────────────────────────

async def wrarity(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if not _is_sudo(user_id):
        await update.message.reply_text("❌ Sudo only.")
        return

    counts = []
    for number, rarity in RARITY_MAP.items():
        count = await waifu_collection.count_documents({"rarity": rarity})
        counts.append(
            f"{_rarity_icon(rarity)} <b>{escape(rarity)}</b> — "
            f"<code>{count}</code> characters"
        )

    total = await waifu_collection.count_documents({})

    text = (
        "📊 <b>Characters by Rarity</b>\n\n"
        + "\n".join(counts)
        + f"\n\n🎴 <b>Total:</b> <code>{total}</code> characters"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────────────────────────────────────────
# /wmode — everyone, rarity filter for their harem
# ─────────────────────────────────────────────────────────────────────────────

async def wmode(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id

    rows = []
    rarities = list(RARITY_MAP.values())
    for i in range(0, len(rarities), 2):
        row = []
        for rarity in rarities[i:i + 2]:
            row.append(
                InlineKeyboardButton(
                    f"{_rarity_icon(rarity)} {rarity.split(' ', 1)[1] if ' ' in rarity else rarity}",
                    callback_data=f"wmode:set:{user_id}:{rarities.index(rarity)}",
                )
            )
        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            "🌈 All Rarities",
            callback_data=f"wmode:all:{user_id}:0",
        )
    ])

    await update.message.reply_text(
        "🎛️ <b>Harem Mode</b>\n\nChoose a rarity to filter your harem:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def wmode_callback(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    parts = q.data.split(":")

    if len(parts) != 4:
        await q.answer("❌ Invalid mode.", show_alert=True)
        return

    _, action, uid_str, value = parts
    if q.from_user.id != int(uid_str):
        await q.answer("❌ That's not your harem!", show_alert=True)
        return

    if action == "menu":
        page = int(value)
        rarities = list(RARITY_MAP.values())
        rows = []

        for i in range(0, len(rarities), 2):
            row = []
            for rarity in rarities[i:i + 2]:
                idx = rarities.index(rarity)
                label = rarity.split(" ", 1)[1] if " " in rarity else rarity
                row.append(
                    InlineKeyboardButton(
                        f"{_rarity_icon(rarity)} {label}",
                        callback_data=f"wmode:set:{uid_str}:{idx}",
                    )
                )
            rows.append(row)

        rows.append([
            InlineKeyboardButton(
                "🌈 All Rarities",
                callback_data=f"wmode:all:{uid_str}:{page}",
            )
        ])

        await q.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(rows)
        )
        await q.answer()
        return

    if action == "all":
        context.user_data.pop("harem_rarity", None)
        await q.answer("🌈 All rarities selected.")
        await harem(update, context, page=0)
        return

    if action == "set":
        rarities = list(RARITY_MAP.values())
        idx = int(value)

        if idx < 0 or idx >= len(rarities):
            await q.answer("❌ Invalid rarity.", show_alert=True)
            return

        rarity = rarities[idx]
        context.user_data["harem_rarity"] = rarity

        await q.answer(f"{_rarity_icon(rarity)} {rarity} selected.")
        await harem(update, context, page=0)
        return

    await q.answer("❌ Unknown mode.", show_alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# /reset <user_id> — OWNER ONLY
# Clears only the target user's harem.
# ─────────────────────────────────────────────────────────────────────────────

async def reset_harem(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only.")
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/reset &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    result = await user_collection.update_one(
        {"id": target_id},
        {"$set": {"characters": []}},
    )

    if result.matched_count == 0:
        await update.message.reply_text("❌ User not found.")
        return

    await update.message.reply_text(
        f"✅ Harem reset for <code>{target_id}</code>.",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────────────────────────────────────
# /kill <char_id> — OWNER ONLY
# Reply to a user's message and remove that character from their harem.
# ─────────────────────────────────────────────────────────────────────────────

async def kill_character(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Reply to the user's message.\n"
            "Usage: <code>/kill &lt;char_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/kill &lt;char_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id = context.args[0].strip()
    target_user = update.message.reply_to_message.from_user

    result = await user_collection.update_one(
        {
            "id": target_user.id,
            "characters": {"$elemMatch": {"id": char_id}},
        },
        {
            "$pull": {"characters": {"id": char_id}},
        },
    )

    if result.matched_count == 0:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> "
            f"not found in {escape(target_user.first_name or str(target_user.id))}'s harem.",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        f"✅ Character <code>{escape(char_id)}</code> removed from "
        f"<b>{escape(target_user.first_name or str(target_user.id))}</b>'s harem.",
        parse_mode=ParseMode.HTML,
    )

# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

application.add_handler(CommandHandler("reset", reset_harem, block=False))
application.add_handler(CommandHandler("kill", kill_character, block=False))

application.add_handler(
    CommandHandler(["harem", "collection"], harem, block=False)
)
application.add_handler(
    CallbackQueryHandler(
        harem_callback,
        pattern=r"^harem:\d+:\d+$",
        block=False,
    )
)
application.add_handler(
    CallbackQueryHandler(
        wmode_callback,
        pattern=r"^wmode:(menu|set|all):\d+:\d+$",
        block=False,
    )
)
application.add_handler(
    CallbackQueryHandler(noop, pattern=r"^noop$", block=False)
)

application.add_handler(CommandHandler("w", w_character, block=False))
application.add_handler(CommandHandler("wrarity", wrarity, block=False))
application.add_handler(CommandHandler("wmode", wmode, block=False))
            
