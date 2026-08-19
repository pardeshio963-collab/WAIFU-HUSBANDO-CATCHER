from html import escape
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, CallbackContext, CallbackQueryHandler, ChatMemberHandler, CommandHandler, MessageHandler, filters
from waifu import application, collection
from waifu.config import Config

_bans = collection.database["global_bans"]

def _owner(update):
    u = update.effective_user
    return bool(u and u.id == Config.OWNER_ID)

def _by(u):
    # Clickable Telegram mention; falls back to the display name if no username exists.
    name = escape(u.first_name or "Owner")
    if u.username:
        return f'<a href="https://t.me/{escape(u.username)}">@{escape(u.username)}</a>'
    return f'<a href="tg://user?id={u.id}">{name}</a>'

async def _owner_only(update):
    if _owner(update):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ This command is owner-only.")
    return False

async def gban(update: Update, context: CallbackContext):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /gban <user_id> [reason]"); return
    try: uid=int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID."); return
    reason=" ".join(context.args[1:]).strip() or "No reason provided"
    by=_by(update.effective_user)
    await _bans.update_one({"type":"user","id":uid},{"$set":{"type":"user","id":uid,"reason":reason,"banned_by":by,"banned_by_id":update.effective_user.id}},upsert=True)
    await update.effective_message.reply_text(
        "🚫 <b>GLOBAL BAN</b>\n\n"
        f"👤 User: <code>{uid}</code>\n🔒 Status: Banned\n"
        f"📝 Reason: {escape(reason)}\n👑 Banned by: {by}\n\n"
        "This user is globally banned from Waifu Nexus.", parse_mode=ParseMode.HTML)

async def ungban(update: Update, context: CallbackContext):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /ungban <user_id>"); return
    try: uid=int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID."); return
    await _bans.delete_one({"type":"user","id":uid})
    await update.effective_message.reply_text(
        "✅ <b>GLOBAL UNBAN</b>\n\n"
        f"👤 User: <code>{uid}</code>\n🔓 Status: Unbanned\n\n"
        "The user can use Waifu Nexus again.", parse_mode=ParseMode.HTML)

async def gcban(update: Update, context: CallbackContext):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /gcban <group_id> [reason]"); return
    try: gid=int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid group ID."); return
    reason=" ".join(context.args[1:]).strip() or "No reason provided"
    by=_by(update.effective_user)
    await _bans.update_one({"type":"group","id":gid},{"$set":{"type":"group","id":gid,"reason":reason,"banned_by":by,"banned_by_id":update.effective_user.id}},upsert=True)
    try: await context.bot.leave_chat(gid)
    except TelegramError: pass
    await update.effective_message.reply_text(
        "🚫 <b>GLOBAL GROUP BAN</b>\n\n"
        f"💬 Group: <code>{gid}</code>\n🔒 Status: Banned\n"
        f"📝 Reason: {escape(reason)}\n👑 Banned by: {by}\n\n"
        "This group is globally banned from Waifu Nexus.", parse_mode=ParseMode.HTML)

async def gcungban(update: Update, context: CallbackContext):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /gcungban <group_id>"); return
    try: gid=int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid group ID."); return
    await _bans.delete_one({"type":"group","id":gid})
    await update.effective_message.reply_text(
        "✅ <b>GLOBAL GROUP UNBAN</b>\n\n"
        f"💬 Group: <code>{gid}</code>\n🔓 Status: Unbanned\n\n"
        "The group can use Waifu Nexus again.", parse_mode=ParseMode.HTML)

async def _block(update: Update, context: CallbackContext):
    u=update.effective_user; c=update.effective_chat
    if u and u.id==Config.OWNER_ID: return
    if u and await _bans.find_one({"type":"user","id":u.id}): raise ApplicationHandlerStop
    if c and c.type in ("group","supergroup") and await _bans.find_one({"type":"group","id":c.id}): raise ApplicationHandlerStop

async def _callback_block(update: Update, context: CallbackContext):
    u=update.effective_user
    if u and u.id!=Config.OWNER_ID and await _bans.find_one({"type":"user","id":u.id}):
        raise ApplicationHandlerStop

async def _rejoin(update: Update, context: CallbackContext):
    c=update.effective_chat
    if c and c.type in ("group","supergroup") and await _bans.find_one({"type":"group","id":c.id}):
        try: await context.bot.leave_chat(c.id)
        except TelegramError: pass

application.add_handler(CommandHandler("gban",gban))
application.add_handler(CommandHandler("ungban",ungban))
application.add_handler(CommandHandler("gcban",gcban))
application.add_handler(CommandHandler("gcungban",gcungban))
application.add_handler(MessageHandler(filters.ALL,_block),group=-100)
application.add_handler(CallbackQueryHandler(_callback_block),group=-100)
application.add_handler(ChatMemberHandler(_rejoin,ChatMemberHandler.MY_CHAT_MEMBER),group=-100)
