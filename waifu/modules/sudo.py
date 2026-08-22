"""Owner-only dynamic sudo management.

Sudo IDs are stored in MongoDB, so adding/removing a sudo user does not
require editing environment variables or redeploying the bot.
"""
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

from waifu import application, OWNER_ID, sudo_users, sudo_collection


def _owner_only(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == OWNER_ID)


async def addsudo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _owner_only(update):
        return

    if len(context.args) != 1 or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /addsudo USER_ID")
        return

    user_id = int(context.args[0])
    if user_id == OWNER_ID:
        await update.message.reply_text("ℹ️ Owner is already a sudo.")
        return

    await sudo_collection.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id}},
        upsert=True,
    )
    sudo_users.add(user_id)
    await update.message.reply_text(
        f"✅ <code>{user_id}</code> added to sudo.",
        parse_mode=ParseMode.HTML,
    )


async def remsudo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _owner_only(update):
        return

    if len(context.args) != 1 or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /remsudo USER_ID")
        return

    user_id = int(context.args[0])
    if user_id == OWNER_ID:
        await update.message.reply_text("❌ Owner cannot be removed.")
        return

    await sudo_collection.delete_one({"user_id": user_id})
    sudo_users.discard(user_id)
    await update.message.reply_text(
        f"✅ <code>{user_id}</code> removed from sudo.",
        parse_mode=ParseMode.HTML,
    )


async def sudolist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _owner_only(update):
        return

    others = sorted(uid for uid in sudo_users if uid != OWNER_ID)
    if not others:
        text = "👑 <b>Owner</b>\nNo additional sudo users."
    else:
        lines = ["👑 <b>Owner</b>", f"<code>{OWNER_ID}</code>", "", "🛡 <b>Sudo Users</b>"]
        lines.extend(f"• <code>{uid}</code>" for uid in others)
        text = "\n".join(lines)

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


application.add_handler(CommandHandler("addsudo", addsudo, block=False))
application.add_handler(CommandHandler("remsudo", remsudo, block=False))
application.add_handler(CommandHandler("sudolist", sudolist, block=False))
