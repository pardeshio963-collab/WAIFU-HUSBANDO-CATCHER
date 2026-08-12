import html
import io
import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import (application, group_user_totals_collection, OWNER_ID,
                   PHOTO_URL, top_global_groups_collection, user_collection)
from waifu import sudo_users as SUDO

_M = ["🥇", "🥈", "🥉"]
_T = 15


def _link(first: str, username: str | None, uid: int) -> str:
    name = html.escape(first[:_T] + ("…" if len(first) > _T else ""))
    if username and username != "Unknown":
        return f'<a href="https://t.me/{username}"><b>{name}</b></a>'
    return f'<a href="tg://user?id={uid}"><b>{name}</b></a>'


def _medal(i: int) -> str:
    return _M[i] if i < 3 else f"{i+1}."


async def leaderboard(update: Update, context: CallbackContext) -> None:
    cursor = user_collection.aggregate([
        {"$project": {
            "username": 1, "first_name": 1, "id": 1,
            "character_count": {"$size": {"$ifNull": ["$characters", []]}},
        }},
        {"$sort": {"character_count": -1}},
        {"$limit": 10},
    ])
    data  = await cursor.to_list(10)
    lines = ["<b>🌸 Top 10 Collectors</b>\n"]
    for i, u in enumerate(data):
        lnk = _link(u.get("first_name") or "?", u.get("username"), u.get("id", 0))
        lines.append(f"{_medal(i)} {lnk} ➾ <b>{u.get('character_count',0)}</b>")
    photo = "https://files.catbox.moe/imj4fh.jpg"
    if photo:
        await update.message.reply_photo(photo, caption="\n".join(lines), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def ctop(update: Update, context: CallbackContext) -> None:
    cid    = update.effective_chat.id
    cursor = group_user_totals_collection.aggregate([
        {"$match": {"group_id": cid}},
        {"$project": {"username": 1, "first_name": 1, "user_id": 1, "character_count": "$count"}},
        {"$sort": {"character_count": -1}},
        {"$limit": 10},
    ])
    data  = await cursor.to_list(10)
    lines = ["<b>🏆 Top 10 in This Group</b>\n"]
    for i, u in enumerate(data):
        lnk = _link(u.get("first_name") or "?", u.get("username"), u.get("user_id", 0))
        lines.append(f"{_medal(i)} {lnk} ➾ <b>{u.get('character_count',0)}</b>")
    photo = "https://files.catbox.moe/imj4fh.jpg"
    if photo:
        await update.message.reply_photo(photo, caption="\n".join(lines), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def global_leaderboard(update: Update, context: CallbackContext) -> None:
    cursor = top_global_groups_collection.aggregate([
        {"$project": {"group_name": 1, "count": 1}},
        {"$sort": {"count": -1}},
        {"$limit": 10},
    ])
    data  = await cursor.to_list(10)
    lines = ["<b>🌍 Top 10 Active Groups</b>\n"]
    for i, g in enumerate(data):
        name  = html.escape((g.get("group_name") or "Unknown")[:_T])
        lines.append(f"{_medal(i)} <b>{name}</b> ➾ <b>{g.get('count',0)}</b>")
    photo = "https://files.catbox.moe/imj4fh.jpg"
    if photo:
        await update.message.reply_photo(photo, caption="\n".join(lines), parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def stats(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Owner only."); return
    users  = await user_collection.count_documents({})
    groups = await top_global_groups_collection.distinct("group_id")
    await update.message.reply_text(
        f"📊 <b>Bot Stats</b>\n\n👤 Users: <b>{users}</b>\n👥 Groups: <b>{len(groups)}</b>",
        parse_mode=ParseMode.HTML,
    )


async def send_users_doc(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in SUDO:
        await update.message.reply_text("❌ Sudo only."); return
    buf = io.BytesIO()
    async for doc in user_collection.find({}, {"first_name": 1, "id": 1}):
        buf.write(f"{doc.get('first_name','?')} ({doc.get('id','?')})\n".encode())
    buf.seek(0); buf.name = "users.txt"
    await context.bot.send_document(update.effective_chat.id, buf)


async def send_groups_doc(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in SUDO:
        await update.message.reply_text("❌ Sudo only."); return
    buf = io.BytesIO()
    async for doc in top_global_groups_collection.find({}, {"group_name": 1, "group_id": 1}):
        buf.write(f"{doc.get('group_name','?')} ({doc.get('group_id','?')})\n".encode())
    buf.seek(0); buf.name = "groups.txt"
    await context.bot.send_document(update.effective_chat.id, buf)


application.add_handler(CommandHandler("top",       leaderboard,       block=False))
application.add_handler(CommandHandler("ctop",      ctop,              block=False))
application.add_handler(CommandHandler("TopGroups", global_leaderboard, block=False))
application.add_handler(CommandHandler("stats",     stats,             block=False))
application.add_handler(CommandHandler("list",      send_users_doc,    block=False))
application.add_handler(CommandHandler("groups",    send_groups_doc,   block=False))
