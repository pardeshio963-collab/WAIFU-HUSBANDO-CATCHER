"""
modules/profile.py — /profile command showing full user stats.
"""
import math
import random
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection, PHOTO_URL


def _xp_for_level(level: int) -> int:
    return int(200 * (level ** 1.5))


def _calc_level(xp: int) -> tuple[int, int, int]:
    """Returns (level, xp_into_level, xp_needed)."""
    level = 1
    while _xp_for_level(level + 1) <= xp:
        level += 1
    floor = _xp_for_level(level)
    nxt   = _xp_for_level(level + 1)
    return level, xp - floor, nxt - floor


def _bar(value: int, maximum: int, length: int = 10) -> str:
    filled = int(length * value / max(maximum, 1))
    return "▓" * filled + "░" * (length - filled)


async def profile(update: Update, context: CallbackContext) -> None:
    # Support /profile or reply to another user
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
    elif context.args:
        # try by username
        username = context.args[0].lstrip("@")
        doc = await user_collection.find_one({"username": username})
        if not doc:
            await update.message.reply_text("❌ User not found.")
            return
        target = None
        u_doc  = doc
    else:
        target = update.effective_user

    if target:
        u_doc = await user_collection.find_one({"id": target.id})

    if not u_doc:
        await update.message.reply_text("❌ That user hasn't played yet.")
        return

    uid        = u_doc["id"]
    first_name = escape(u_doc.get("first_name", "User"))
    username   = u_doc.get("username")
    coins      = u_doc.get("coins", 0)
    chars      = u_doc.get("characters", [])
    wins       = u_doc.get("wins", 0)
    guesses    = u_doc.get("total_guesses", 0)
    xp         = u_doc.get("xp", 0)
    fav_id     = (u_doc.get("favorites") or [None])[0]

    unique_count = len({c["id"] for c in chars})
    total_count  = len(chars)

    # Rarity breakdown
    rarity_count: dict[str, int] = {}
    for c in {c["id"]: c for c in chars}.values():
        r = c.get("rarity", "Unknown")
        rarity_count[r] = rarity_count.get(r, 0) + 1

    level, xp_in, xp_need = _calc_level(xp)
    bar = _bar(xp_in, xp_need, 12)

    # Collection value (sum of rarity weights)
    VALUE_MAP = {"⚪ Common": 100, "🟢 Medium": 300, "🟣 Rare": 600,
                 "🟡 Legendary": 1500, "💮 Special Edition": 5000}
    total_value = sum(
        VALUE_MAP.get(c.get("rarity", ""), 100)
        for c in {c["id"]: c for c in chars}.values()
    )

    tag = f"@{username}" if username else f"#{uid}"
    rarity_lines = "\n".join(
        f"  {r}: {n}" for r, n in sorted(rarity_count.items())
    ) or "  None yet"

    text = (
        f"👤 <b>{first_name}</b>  <code>{tag}</code>\n"
        f"{'─' * 28}\n"
        f"⭐ Level <b>{level}</b>  [{bar}]\n"
        f"   <i>{xp_in:,} / {xp_need:,} XP</i>\n\n"
        f"💰 Coins:     <b>{coins:,}</b>\n"
        f"🗂 Collection: <b>{unique_count}</b> unique  ({total_count} total)\n"
        f"💎 Est. value: <b>{total_value:,}</b> coins\n"
        f"🎯 Guesses:   <b>{guesses}</b>\n"
        f"⚔️ Duel wins: <b>{wins}</b>\n\n"
        f"<b>Rarity breakdown:</b>\n{rarity_lines}"
    )

    # Show favourite character image if available
photo: str | None = None

if fav_id:
    fav_char = next((c for c in chars if c["id"] == fav_id), None)
    photo = random.choice(PHOTO_URL)

if not photo:
    photo = "https://files.catbox.moe/lly67s.jpg"

if photo:
    await update.message.reply_photo(
        photo, caption=text, parse_mode=ParseMode.HTML
    )
else:
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML
    )


application.add_handler(CommandHandler("profile", profile, block=False))
