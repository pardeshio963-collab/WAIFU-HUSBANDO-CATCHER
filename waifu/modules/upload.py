"""
modules/upload.py

Two upload methods:

1) /upload IMG_URL character-name anime-name rarity_number
   Classic command-line upload.

2) /uploadchar  (reply to a channel post whose caption is in the format):
   🍀 Name: Sasha Braus
   🍋 Rarity: Legendary
   🌸 Anime: Attack On Titan
   🌱 ID: 26          ← optional; auto-generated if absent

   The replied-to message must have a photo attached.

Both methods post to CHARA_CHANNEL_ID and insert into the DB.
"""
import re

import aiohttp
from pymongo import ReturnDocument
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, db, sudo_users, OWNER_ID, CHARA_CHANNEL_ID
from waifu.config import Config

# Harem/upload rarity list — keep these 8 only.
RARITY_MAP = {
    1: "⚪ Common",
    2: "🟣 Rare",
    3: "🟡 Legendary",
    4: "🪁 Skyrise",
    5: "💮 Exclusive",
    6: "🔮 Mythical",
    7: "🫧 Special",
    8: "🌤️ Summer",
    9: "🧧 Limited",
}
EDITION_MAP = Config.EDITION_MAP
RARITY_STRS = {v.lower(): v for v in RARITY_MAP.values()}
EDITION_STRS = {v.lower(): v for v in EDITION_MAP.values()}

WRONG_FORMAT = (
    "❌ Wrong format\n\n"
    "<code>/upload IMG_URL character-name anime-name rarity_number</code>\n\n"
    "<b>Rarity numbers:</b>\n"
    + "\n".join(f"  {k} → {v}" for k, v in RARITY_MAP.items())
)


def _is_sudo(uid: int) -> bool:
    return uid in sudo_users or uid == OWNER_ID


async def _validate_url(url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.head(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                return r.status < 400
    except Exception:
        return False


async def _next_id() -> str:
    doc = await db.sequences.find_one_and_update(
        {"_id": "character_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return str(doc["sequence_value"]).zfill(4)


def _char_caption(char: dict, uploader_id: int, uploader_name: str) -> str:
    return (
        f"🍀 <b>Name:</b> {char['name']}\n"
        f"🍋 <b>Rarity:</b> {char['rarity']}\n"
        f"🎀 <b>Edition:</b> {char.get('edition', 'None')}\n"
        f"🌸 <b>Anime:</b> {char['anime']}\n"
        f"🌱 <b>ID:</b> {char['id']}\n\n"
        f"Added by <a href='tg://user?id={uploader_id}'>{uploader_name}</a>"
    )


# ── Method 1: /upload ─────────────────────────────────────────────────────────

async def upload(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    if len(context.args) not in (3, 4):
        await update.message.reply_text(
            "❌ Wrong format\n\n"
            "<code>/upload name anime rarity [edition]</code>\n\n"
            "💡 Edition is optional.\n"
            "<i>Reply to the character image when using /upload.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text("❌ Reply to the character image with /upload.")
        return

    photo = None
    if replied.photo:
        photo = replied.photo[-1].file_id
    elif replied.document and (replied.document.mime_type or "").startswith("image/"):
        photo = replied.document.file_id

    if not photo:
        await update.message.reply_text("❌ The replied message must contain an image.")
        return

    raw_name, raw_anime, raw_rarity = context.args[:3]
    raw_edition = context.args[3] if len(context.args) == 4 else None

    try:
        rarity = RARITY_MAP[int(raw_rarity)]
    except (KeyError, ValueError):
        await update.message.reply_text(
            f"❌ Invalid rarity number. Use 1–{len(RARITY_MAP)}.",
            parse_mode=ParseMode.HTML,
        )
        return

    edition = None
    if raw_edition is not None:
        try:
            edition = EDITION_MAP[int(raw_edition)]
        except (KeyError, ValueError):
            await update.message.reply_text(
                f"❌ Invalid edition number. Use 1–{len(EDITION_MAP)}.",
                parse_mode=ParseMode.HTML,
            )
            return

    name = raw_name.replace("-", " ").title()
    anime = raw_anime.replace("-", " ").title()
    char_id = await _next_id()

    char = {
        "img_url": photo,
        "name": name,
        "anime": anime,
        "rarity": rarity,
        "edition": edition,
        "id": char_id,
    }

    try:
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=photo,
            caption=_char_caption(
                char, update.effective_user.id, update.effective_user.first_name
            ),
            parse_mode=ParseMode.HTML,
        )
        char["message_id"] = msg.message_id
        await collection.insert_one(char)
        success = (
            f"✅ <b>{name}</b> added!\n"
            f"🆔 ID: <code>{char_id}</code>\n"
            f"🍋 Rarity: {rarity}"
        )
        if edition:
            success += f"\n🎀 Edition: {edition}"

        await update.message.reply_text(success, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Channel post failed: {e}\nCharacter was <b>not</b> saved.",
            parse_mode=ParseMode.HTML,
        )


# ── Method 2: /uploadchar (reply to formatted post) ──────────────────────────

def _parse_caption(caption: str) -> dict | None:
    """
    Parse a caption like:
        🍀 Name: Sasha Braus
        🍋 Rarity: Legendary
        🌸 Anime: Attack On Titan
        🌱 ID: 26
    Returns dict with keys: name, rarity, anime, id (id may be None).
    """
    fields: dict[str, str] = {}
    patterns = {
        "name":   r"(?:🍀\s*)?Name\s*:\s*(.+)",
        "rarity": r"(?:🍋\s*)?Rarity\s*:\s*(.+)",
        "edition": r"(?:🎀\s*)?Edition\s*:\s*(.+)",
        "anime":  r"(?:🌸\s*)?Anime\s*:\s*(.+)",
        "id":     r"(?:🌱\s*)?ID\s*:\s*(\S+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, caption, re.IGNORECASE)
        if m:
            fields[key] = m.group(1).strip()

    if "name" not in fields or "anime" not in fields:
        return None

    # Normalise rarity string to our canonical format
    raw_rarity = fields.get("rarity", "").lower()
    raw_edition = fields.get("edition", "").lower()
    # Try exact match first
    rarity = RARITY_STRS.get(raw_rarity)
    if not rarity:
        # Partial match (e.g. "legendary" inside "🟡 Legendary")
        for key, val in RARITY_STRS.items():
            if raw_rarity in key or key in raw_rarity:
                rarity = val
                break
    if not rarity:
        rarity = RARITY_MAP.get(1, "⚪ Legendary")

    edition = EDITION_STRS.get(raw_edition)
    if not edition:
        for key, val in EDITION_STRS.items():
            if raw_edition and (raw_edition in key or key in raw_edition):
                edition = val
                break

    return {
        "name":    fields["name"].title(),
        "anime":   fields["anime"].title(),
        "rarity":  rarity,
        "edition": edition,
        "id":      fields.get("id"),       # None → auto-generate
    }


async def uploadchar(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    replied = update.message.reply_to_message
    if not replied:
        await update.message.reply_text(
            "❌ Reply to a post that has the character's image and caption.\n\n"
            "<b>Expected caption format:</b>\n"
            "🍀 Name: Character Name\n"
            "🍋 Rarity: Legendary\n"
            "🌸 Anime: Anime Name\n"
            "🌱 ID: 26  <i>(optional)</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    # The replied message must have a photo
    photo = None
    if replied.photo:
        photo = replied.photo[-1].file_id   # highest resolution
    elif replied.document and replied.document.mime_type.startswith("image/"):
        photo = replied.document.file_id
    else:
        await update.message.reply_text(
            "❌ The replied message must contain an image.")
        return

    caption = replied.caption or replied.text or ""
    parsed  = _parse_caption(caption)
    if not parsed:
        await update.message.reply_text(
            "❌ Could not parse the caption. Make sure it contains at least "
            "<b>Name</b> and <b>Anime</b> fields.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Use the ID from the caption if provided and not already taken, else auto
    if parsed["id"]:
        existing = await collection.find_one({"id": parsed["id"]})
        if existing:
            await update.message.reply_text(
                f"❌ ID <code>{parsed['id']}</code> already exists in the database.",
                parse_mode=ParseMode.HTML,
            )
            return
        char_id = parsed["id"]
    else:
        char_id = await _next_id()

    char = {
        "img_url": photo,           # Telegram file_id (no expiry for bot-uploaded files)
        "name":    parsed["name"],
        "anime":   parsed["anime"],
        "rarity":  parsed["rarity"],
        "edition": parsed.get("edition"),
        "id":      char_id,
    }

    try:
        msg = await context.bot.send_photo(
            chat_id=CHARA_CHANNEL_ID,
            photo=photo,
            caption=_char_caption(char, update.effective_user.id, update.effective_user.first_name),
            parse_mode=ParseMode.HTML,
        )
        char["message_id"] = msg.message_id
        await collection.insert_one(char)
        await update.message.reply_text(
            f"✅ <b>{parsed['name']}</b> uploaded!\n"
            f"🎴 Rarity: {parsed['rarity']}\n"
            f"📺 Anime: {parsed['anime']}\n"
            f"🆔 ID: <code>{char_id}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Channel post failed: {e}\nCharacter was <b>not</b> saved.",
            parse_mode=ParseMode.HTML,
        )


# ── /rarities and /editions ─────────────────────────────────────────────────────

async def rarities(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    text = "<b>🍋 Rarity List</b>\n\n" + "\n".join(
        f"<code>{k}</code> → {v}" for k, v in RARITY_MAP.items()
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def editions(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return

    text = "<b>🎀 Edition List</b>\n\n" + "\n".join(
        f"<code>{k}</code> → {v}" for k, v in EDITION_MAP.items()
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /delete ───────────────────────────────────────────────────────────────────

async def delete(update: Update, context: CallbackContext) -> None:
    if not _is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ Sudo only.")
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/delete ID</code>", parse_mode=ParseMode.HTML)
        return

    char = await collection.find_one_and_delete({"id": context.args[0]})
    if not char:
        await update.message.reply_text("❌ Character not found.")
        return
    if char.get("message_id"):
        try:
            await context.bot.delete_message(CHARA_CHANNEL_ID, char["message_id"])
        except Exception:
            pass
    await update.message.reply_text(
        f"✅ Deleted <b>{char['name']}</b> (<code>{char['id']}</code>)",
        parse_mode=ParseMode.HTML,
    )


# ── /update ───────────────────────────────────────────────────────────────────

_VALID = {"img_url", "name", "anime", "rarity", "edition"}


async def update_char(upd: Update, context: CallbackContext) -> None:
    if not _is_sudo(upd.effective_user.id):
        await upd.message.reply_text("❌ Sudo only.")
        return
    if len(context.args) != 3:
        await upd.message.reply_text(
            "Usage: <code>/update ID field new_value</code>\n"
            f"Fields: {', '.join(_VALID)}",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id, field, raw = context.args
    if field not in _VALID:
        await upd.message.reply_text(f"❌ Invalid field. Choose: {', '.join(_VALID)}")
        return

    char = await collection.find_one({"id": char_id})
    if not char:
        await upd.message.reply_text("❌ Character not found.")
        return

    if field in ("name", "anime"):
        new_val = raw.replace("-", " ").title()
    elif field == "rarity":
        try:
            new_val = RARITY_MAP[int(raw)]
        except (KeyError, ValueError):
            await upd.message.reply_text(f"❌ Invalid rarity. Use 1–{len(RARITY_MAP)}.")
            return
    elif field == "edition":
        try:
            new_val = EDITION_MAP[int(raw)]
        except (KeyError, ValueError):
            await upd.message.reply_text(f"❌ Invalid edition. Use 1–{len(EDITION_MAP)}.")
            return
    elif field == "img_url":
        if not await _validate_url(raw):
            await upd.message.reply_text("❌ Invalid or unreachable image URL.")
            return
        new_val = raw
    else:
        new_val = raw

    await collection.update_one({"id": char_id}, {"$set": {field: new_val}})
    char[field] = new_val

    try:
        if field == "img_url":
            if char.get("message_id"):
                await context.bot.delete_message(CHARA_CHANNEL_ID, char["message_id"])
            msg = await context.bot.send_photo(
                CHARA_CHANNEL_ID, photo=new_val,
                caption=_char_caption(char, upd.effective_user.id, upd.effective_user.first_name),
                parse_mode=ParseMode.HTML,
            )
            await collection.update_one({"id": char_id}, {"$set": {"message_id": msg.message_id}})
        elif char.get("message_id"):
            await context.bot.edit_message_caption(
                CHARA_CHANNEL_ID, char["message_id"],
                caption=_char_caption(char, upd.effective_user.id, upd.effective_user.first_name),
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        await upd.message.reply_text(f"⚠️ DB updated but channel sync failed: {e}")
        return

    await upd.message.reply_text(
        f"✅ <b>{char['name']}</b> — <code>{field}</code> updated.",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("upload",     upload,      block=False))
application.add_handler(CommandHandler("uploadchar", uploadchar,  block=False))
application.add_handler(CommandHandler("rarities",   rarities,    block=False))
application.add_handler(CommandHandler("editions",   editions,    block=False))
application.add_handler(CommandHandler("delete",     delete,      block=False))
application.add_handler(CommandHandler("update",     update_char, block=False))
   
