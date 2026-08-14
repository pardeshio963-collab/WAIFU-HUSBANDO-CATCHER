"""
modules/harem.py — Paginated collection with inline action buttons per character.
"""
import math
from html import escape
from itertools import groupby

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, waifu_collection

_PAGE = 15
_MEDALS = {
    "⚪ Common": "⚪",
    "🟣 Rare": "🟣",
    "🟡 Legendary": "🟡",
    "🪁 Skyrise": "🪁",
    "💮 Exclusive": "💮",
    "🔮 Mythical": "🔮",
    "🫧 Special": "🫧",
    "🌤️ Summer": "🌤️",
}

def _rarity_icon(rarity: str) -> str:
    return _MEDALS.get(rarity, "🎴")


_EDITION_ICONS = {
    "🎃 Halloween": "🎃", "💕 Valentine": "💕", "🩺 Doctor": "🩺",
    "🐞 Bug": "🐞", "🧘 Monk": "🧘", "🏀 Basketball": "🏀",
    "👶 Chibi": "👶", "👘 Kimono": "👘", "☕ Coffee": "☕",
    "🌈 Holi": "🌈", "🧜 Mermaid": "🧜", "🥻 Saree": "🥻",
    "🫥 Abses": "🫥", "🎀 Maid": "🎀", "🎵 Music": "🎵",
    "❄️ Winter": "❄️", "🪔 Diwali": "🪔", "🎮 Game": "🎮",
    "🎄 Xmas": "🎄", "☀️ Summer": "☀️",
}

def _edition_text(edition: str | None) -> str:
    if not edition:
        return ""
    # Stored edition values are already canonical in the upload system.
    return f"🎀 {escape(str(edition))}"


async def _anime_totals(animes: list[str]) -> dict[str, int]:
    pipeline = [
        {"$match": {"anime": {"$in": animes}}},
        {"$group": {"_id": "$anime", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in waifu_collection.aggregate(pipeline)}


async def _build_page(user_id: int, page: int) -> tuple[str, InlineKeyboardMarkup, str | None, list[dict]]:
    """Returns (text, keyboard, photo_url)."""
    user = await user_collection.find_one({"id": user_id})
    if not user or not user.get("characters"):
        return "📭 Your harem is empty — go catch some characters!", InlineKeyboardMarkup([]), None, []

    chars = user["characters"]
    # Deduplicate keeping all counts
    id_counts: dict[str, int] = {}
    for c in chars:
        id_counts[c["id"]] = id_counts.get(c["id"], 0) + 1

    unique: list[dict] = list({c["id"]: c for c in chars}.values())
    unique.sort(key=lambda x: (x["anime"], x["id"]))

    total_unique = len(unique)
    total_pages  = max(1, math.ceil(total_unique / _PAGE))
    page = max(0, min(page, total_pages - 1))

    page_chars  = unique[page * _PAGE:(page + 1) * _PAGE]
    animes      = list({c["anime"] for c in page_chars})
    db_totals   = await _anime_totals(animes)

    # Header
    fav_id = (user.get("favorites") or [None])[0]
    lines  = [
        f"<b>🌸 {escape(user.get('first_name', 'User'))}'s Harem</b>",
        f"📦 {total_unique} unique  |  🗂 {len(chars)} total  |  "
        f"💰 {user.get('coins', 0):,} coins",
        f"Page {page+1}/{total_pages}\n",
    ]

    # Group by anime
    sorted_page = sorted(page_chars, key=lambda x: x["anime"])
    for anime, group_iter in groupby(sorted_page, key=lambda x: x["anime"]):
        group_list = list(group_iter)
        db_total   = db_totals.get(anime, "?")
        lines.append(f"\n<b>{escape(anime)}  {len(group_list)}/{db_total}</b>")
        for c in group_list:
            icon  = _rarity_icon(c.get("rarity", ""))
            cnt   = id_counts.get(c["id"], 1)
            dup   = f" ×{cnt}" if cnt > 1 else ""
            fav   = " ⭐" if c["id"] == fav_id else ""
            lines.append(f"  {icon} <code>{c['id']}</code> {escape(c['name'])}{dup}{fav}")

    text = "\n".join(lines)

    # Keyboard: collection link + navigation + quick-action for fav char
    kb: list[list] = []
    kb.append([InlineKeyboardButton(
        f"🔍 Browse Collection ({len(chars)})",
        switch_inline_query_current_chat=f"collection.{user_id}",
    )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"harem:{page-1}:{user_id}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"harem:{page+1}:{user_id}"))
    if len(nav) > 1:
        kb.append(nav)

    markup = InlineKeyboardMarkup(kb)

    # Stable photo: fav > first unique
    photo: str | None = None
    if fav_id:
        fav_char = next((c for c in chars if c["id"] == fav_id), None)
        photo    = (fav_char or {}).get("img_url")
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


async def _reply_harem(update: Update, context: CallbackContext, text: str,
                       markup: InlineKeyboardMarkup, photo: str | None,
                       chars: list[dict]) -> None:
    """Send the Harem summary plus every character image for this page."""
    user_id = update.effective_user.id
    is_cb = bool(update.callback_query)

    if not is_cb:
        await _delete_album(context, user_id)
        # Keep the Harem summary as a normal text message so every character
        # image can be shown below it without the old single-photo loading issue.
        msg = await update.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
        context.user_data["harem_summary_id"] = msg.message_id
        return

    try:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=markup
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def harem(update: Update, context: CallbackContext, page: int = 0) -> None:
    user_id = update.effective_user.id
    text, markup, photo, page_chars = await _build_page(user_id, page)
    await _reply_harem(update, context, text, markup, photo, page_chars)


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


application.add_handler(CommandHandler(["harem", "collection"], harem, block=False))
application.add_handler(CallbackQueryHandler(harem_callback, pattern=r"^harem:\d+:\d+$", block=False))
application.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$", block=False))
    
