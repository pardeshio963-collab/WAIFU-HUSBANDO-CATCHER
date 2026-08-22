"""Owner-only character administration: /give and /transfer."""
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection, waifu_collection, OWNER_ID


def _owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def give_character(update: Update, context: CallbackContext) -> None:
    """Owner replies to a user's message: /give CHARACTER_ID"""
    if not _owner(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Reply to the user you want to give the character to.\n"
            "Usage: <code>/give CHARACTER_ID</code>", parse_mode=ParseMode.HTML)
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/give CHARACTER_ID</code>", parse_mode=ParseMode.HTML)
        return

    char_id = context.args[0].strip()
    target = update.message.reply_to_message.from_user
    char = await waifu_collection.find_one({"id": char_id})
    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> not found.", parse_mode=ParseMode.HTML)
        return

    clean = dict(char)
    clean.pop("_id", None)
    await user_collection.update_one(
        {"id": target.id},
        {
            "$setOnInsert": {
                "id": target.id, "username": target.username,
                "first_name": target.first_name, "characters": [],
                "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0, "favorites": [],
            },
            "$push": {"characters": clean},
        },
        upsert=True,
    )
    await update.message.reply_text(
        f"✅ Gave <b>{escape(str(clean.get('name', 'Unknown')))}</b> to "
        f"<a href='tg://user?id={target.id}'>{escape(target.first_name)}</a>.\n"
        f"🆔 <code>{escape(char_id)}</code>", parse_mode=ParseMode.HTML)


async def transfer_harem(update: Update, context: CallbackContext) -> None:
    """Owner replies to source user and supplies target username: /transfer @username"""
    if not _owner(update.effective_user.id):
        await update.message.reply_text("❌ Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Reply to the source user's message.\n"
            "Usage: <code>/transfer @target_username</code>", parse_mode=ParseMode.HTML)
        return
    if len(context.args) != 1 or not context.args[0].startswith("@"):
        await update.message.reply_text(
            "Usage: <code>/transfer @target_username</code>\n"
            "(Reply to the user whose harem should be transferred.)",
            parse_mode=ParseMode.HTML)
        return

    source = update.message.reply_to_message.from_user
    username = context.args[0].lstrip("@").strip()
    if not username:
        await update.message.reply_text("❌ Invalid target username.")
        return

    if source.username and source.username.casefold() == username.casefold():
        await update.message.reply_text("❌ Source and target are the same user.")
        return

    target = await user_collection.find_one({"username": {"$regex": f"^{__import__('re').escape(username)}$", "$options": "i"}})
    if not target:
        try:
            chat = await context.bot.get_chat(f"@{username}")
            target = {"id": chat.id, "username": chat.username, "first_name": chat.first_name}
        except Exception:
            await update.message.reply_text("❌ Target user not found. They need to have used the bot before.")
            return

    target_id = int(target["id"])
    if target_id == source.id:
        await update.message.reply_text("❌ Source and target are the same user.")
        return

    source_doc = await user_collection.find_one({"id": source.id})
    chars = list((source_doc or {}).get("characters", []))
    if not chars:
        await update.message.reply_text("❌ Source user's harem is empty.")
        return

    # Move the complete harem, preserving duplicates. Existing target characters remain.
    await user_collection.update_one(
        {"id": target_id},
        {
            "$setOnInsert": {
                "id": target_id,
                "username": target.get("username"),
                "first_name": target.get("first_name", username),
                "characters": [],
                "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0, "favorites": [],
            },
            "$push": {"characters": {"$each": chars}},
        },
        upsert=True,
    )
    await user_collection.update_one({"id": source.id}, {"$set": {"characters": []}})

    await update.message.reply_text(
        f"✅ Transferred <b>{len(chars)}</b> character copies from "
        f"<a href='tg://user?id={source.id}'>{escape(source.first_name)}</a> "
        f"to <a href='tg://user?id={target_id}'>{escape(target.get('first_name', username))}</a>.",
        parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("give", give_character, block=False))
application.add_handler(CommandHandler("transfer", transfer_harem, block=False))
