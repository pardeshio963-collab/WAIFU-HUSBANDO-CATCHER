"""
modules/economy.py — Daily coins, balance, and marketplace.
"""
import math
import time
import random
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler

from waifu import application, user_collection, market_collection, collection
from waifu.config import Config

_DAILY_COOLDOWN = 86_400   # 24 hours in seconds
_PAGE = 8

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(secs: int) -> str:
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


async def _ensure_user(user_id: int, u) -> dict:
    doc = await user_collection.find_one({"id": user_id})
    if not doc:
        doc = {
            "id": user_id, "username": u.username,
            "first_name": u.first_name, "characters": [],
            "coins": 0, "xp": 0, "wins": 0,
            "total_guesses": 0, "favorites": [],
        }
        await user_collection.insert_one(doc)
    return doc


# ── /balance ──────────────────────────────────────────────────────────────────

async def balance(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    await update.message.reply_text(
        f"💰 <b>{escape(u.first_name)}'s Balance</b>\n\n"
        f"Coins: <b>{doc.get('coins', 0):,}</b> 🪙",
        parse_mode=ParseMode.HTML,
    )


# ── /daily ────────────────────────────────────────────────────────────────────

async def daily(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)
    now = time.time()
    last = doc.get("last_daily", 0)

    if now - last < _DAILY_COOLDOWN:
        remaining = int(_DAILY_COOLDOWN - (now - last))
        await update.message.reply_text(
            f"⏳ Daily already claimed!\nCome back in <b>{_fmt_time(remaining)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    reward = Config.DAILY_COINS
    await user_collection.update_one(
        {"id": u.id},
        {"$inc": {"coins": reward}, "$set": {"last_daily": now}},
    )
    await update.message.reply_text(
        f"🎁 <b>Daily reward!</b>\n\n"
        f"You received <b>{reward:,} coins</b> 🪙\n"
        f"Current balance: <b>{doc.get('coins', 0) + reward:,}</b>",
        parse_mode=ParseMode.HTML,
    )


# ── /sell ─────────────────────────────────────────────────────────────────────

async def sell(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: <code>/sell [char_id] [price]</code>", parse_mode=ParseMode.HTML)
        return

    char_id, price_str = context.args
    if not price_str.isdigit() or int(price_str) <= 0:
        await update.message.reply_text("❌ Price must be a positive number.")
        return
    price = int(price_str)

    doc = await user_collection.find_one({"id": u.id})
    if not doc:
        await update.message.reply_text("❌ You have no characters.")
        return

    char = next((c for c in doc.get("characters", []) if c["id"] == char_id), None)
    if not char:
        await update.message.reply_text("❌ That character isn't in your collection.")
        return

    # Remove from user's harem (escrow while listed)
    await user_collection.update_one(
        {"id": u.id},
        {"$pull": {"characters": {"id": char_id}}},
    )
    listing = {
        "seller_id":   u.id,
        "seller_name": u.first_name,
        "char_id":     char_id,
        "char":        char,
        "price":       price,
        "listed_at":   time.time(),
    }
    result = await market_collection.insert_one(listing)

    await update.message.reply_text(
        f"🏪 <b>{escape(char['name'])}</b> listed for <b>{price:,} coins</b>!\n"
        f"Listing ID: <code>{result.inserted_id}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── /market ───────────────────────────────────────────────────────────────────

async def market(update: Update, context: CallbackContext, page: int = 0) -> None:
    args  = context.args if hasattr(context, "args") and context.args else []
    if args and args[0].isdigit():
        page = int(args[0]) - 1

    total   = await market_collection.count_documents({})
    if total == 0:
        await update.message.reply_text("🏪 The market is empty right now.")
        return

    total_pages = max(1, math.ceil(total / _PAGE))
    page = max(0, min(page, total_pages - 1))

    listings = await market_collection.find({}).sort("price", 1).skip(page * _PAGE).limit(_PAGE).to_list(_PAGE)

    lines = [f"🏪 <b>Market</b>  (page {page+1}/{total_pages})\n"]
    for lst in listings:
        char  = lst["char"]
        lines.append(
            f"{char.get('rarity','🎴')}  <b>{escape(char['name'])}</b>  "
            f"— <b>{lst['price']:,} 🪙</b>\n"
            f"   Seller: {escape(lst['seller_name'])}  |  "
            f"<code>/buy {lst['_id']}</code>"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"market:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"market:{page+1}"))

    kb = InlineKeyboardMarkup([nav] if nav else [])
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)


async def market_page_cb(update: Update, context: CallbackContext) -> None:
    q = update.callback_query
    await q.answer()
    page = int(q.data.split(":")[1])

    total       = await market_collection.count_documents({})
    total_pages = max(1, math.ceil(total / _PAGE))
    page        = max(0, min(page, total_pages - 1))
    listings    = await market_collection.find({}).sort("price", 1).skip(page * _PAGE).limit(_PAGE).to_list(_PAGE)

    lines = [f"🏪 <b>Market</b>  (page {page+1}/{total_pages})\n"]
    for lst in listings:
        char = lst["char"]
        lines.append(
            f"{char.get('rarity','🎴')}  <b>{escape(char['name'])}</b>  "
            f"— <b>{lst['price']:,} 🪙</b>\n"
            f"   Seller: {escape(lst['seller_name'])}  |  "
            f"<code>/buy {lst['_id']}</code>"
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"market:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"market:{page+1}"))
    kb = InlineKeyboardMarkup([nav])
    try:
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass


# ── /buy ──────────────────────────────────────────────────────────────────────

async def buy(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/buy [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found or already sold.")
        return
    if listing["seller_id"] == u.id:
        await update.message.reply_text("❌ You can't buy your own listing.")
        return

    buyer = await user_collection.find_one({"id": u.id})
    if not buyer or buyer.get("coins", 0) < listing["price"]:
        await update.message.reply_text(
            f"❌ Not enough coins. Need <b>{listing['price']:,}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Atomic exchange
    await user_collection.update_one({"id": u.id},
        {"$inc": {"coins": -listing["price"]},
         "$push": {"characters": listing["char"]}})
    await user_collection.update_one({"id": listing["seller_id"]},
        {"$inc": {"coins": listing["price"]}})
    await market_collection.delete_one({"_id": oid})

    char = listing["char"]
    await update.message.reply_text(
        f"✅ You bought <b>{escape(char['name'])}</b> for <b>{listing['price']:,} 🪙</b>!",
        parse_mode=ParseMode.HTML,
    )


# ── /delist ───────────────────────────────────────────────────────────────────

async def delist(update: Update, context: CallbackContext) -> None:
    u = update.effective_user
    if not context.args:
        await update.message.reply_text("Usage: <code>/delist [listing_id]</code>", parse_mode=ParseMode.HTML)
        return

    try:
        oid = ObjectId(context.args[0])
    except Exception:
        await update.message.reply_text("❌ Invalid listing ID.")
        return

    listing = await market_collection.find_one({"_id": oid})
    if not listing:
        await update.message.reply_text("❌ Listing not found.")
        return
    if listing["seller_id"] != u.id:
        await update.message.reply_text("❌ That's not your listing.")
        return

    await market_collection.delete_one({"_id": oid})
    await user_collection.update_one(
        {"id": u.id}, {"$push": {"characters": listing["char"]}})
    await update.message.reply_text("✅ Listing removed. Character returned to your harem.")



# ── /claim ───────────────────────────────────────────────────────────────────

async def claim(update: Update, context: CallbackContext) -> None:
    u   = update.effective_user
    doc = await _ensure_user(u.id, u)

    now = datetime.now(timezone.utc)
    last_claim = doc.get("last_claim")

    if last_claim:
        if isinstance(last_claim, str):
            last_claim = datetime.fromisoformat(last_claim)

        # Normalize legacy naive timestamps to UTC-aware datetimes.
        # This prevents: "can't compare offset-naive and offset-aware datetimes".
        if last_claim.tzinfo is None:
            last_claim = last_claim.replace(tzinfo=timezone.utc)
        else:
            last_claim = last_claim.astimezone(timezone.utc)

        next_claim = last_claim + timedelta(hours=24)

        if now < next_claim:
            remaining = next_claim - now
            total_seconds = int(remaining.total_seconds())
            hours, rem = divmod(total_seconds, 3600)
            minutes = rem // 60

            await update.message.reply_text(
                f"⏳ You already claimed a character today!\n"
                f"🕒 Next /claim in <b>{hours}h {minutes}m</b>.",
                parse_mode=ParseMode.HTML,
            )
            return

    # Weighted rarity selection
    roll = random.randint(1, 100)

    if roll <= 35:
        rarity = "⚪ Common"
    elif roll <= 70:
        rarity = "🟡 Legendary"
    else:
        rarity = "🟣 Rare"

    chars = [c async for c in collection.aggregate([
        {"$match": {"rarity": rarity}},
        {"$sample": {"size": 1}}
    ])]

    if not chars:
        await update.message.reply_text(
            f"⚠️ No {rarity} characters found in database."
        )
        return

    char = chars[0]

    await user_collection.update_one(
        {"id": u.id},
        {
            "$push": {"characters": char},
            "$set": {
                "last_claim": now,
                "username": u.username,
                "first_name": u.first_name,
            },
        },
        upsert=True,
    )

    edition = char.get("edition")
    edition_text = f"\n🎀 {escape(str(edition))}" if edition else ""

    caption = (
        f"🎁 <b>Daily Character Claim!</b>\n\n"
        f"🌸 <b>{escape(char['name'])}</b>\n"
        f"📺 {escape(char['anime'])}\n"
        f"💎 {escape(char.get('rarity', 'Unknown'))}"
        f"{edition_text}\n\n"
        f"🆔 <code>{char['id']}</code>\n\n"
        f"Added to your harem! ✨\n"
        f"⏰ Next free character in <b>24 hours</b>."
    )

    img = char.get("img_url")

    if img:
        await update.message.reply_photo(
            photo=img,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            caption,
            parse_mode=ParseMode.HTML,
        )


# ── /check ────────────────────────────────────────────────────────────────────

async def check(update: Update, context: CallbackContext) -> None:
    """Show the top 10 owners of a character."""
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/check [character name or ID]</code>\n\n"
            "Example: <code>/check Naruto</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text(
            "❌ Please enter a character name or ID.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Find users whose character collection contains the requested character.
    # Matching is case-insensitive for names and exact for IDs.
    users = []
    async for doc in user_collection.find(
        {"characters": {"$exists": True, "$ne": []}},
        {"id": 1, "username": 1, "first_name": 1, "characters": 1},
    ):
        characters = doc.get("characters", [])
        owned = [
            c for c in characters
            if str(c.get("id", "")) == query
            or str(c.get("name", "")).casefold() == query.casefold()
        ]

        if owned:
            # Count copies by character ID/name match.
            count = len(owned)
            char = owned[0]
            users.append((count, doc, char))

    if not users:
        await update.message.reply_text(
            f"❌ No owners found for <b>{escape(query)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    users.sort(key=lambda item: (-item[0], item[1].get("first_name", "").casefold()))

    char = users[0][2]
    char_name = char.get("name", query)
    rarity = char.get("rarity", "🎴 Unknown")
    char_id = char.get("id", "—")

    lines = [
        "╭━━━━━━━━━━━━━━━━━━╮",
        "✨ <b>CHARACTER OWNERS</b> ✨",
        "╰━━━━━━━━━━━━━━━━━━╯",
        "",
        f"🎴 <b>{escape(str(char_name))}</b>",
        f"💎 <b>{escape(str(rarity))}</b>",
        f"🆔 <code>{escape(str(char_id))}</code>",
        "",
        "🏆 <b>TOP 10 OWNERS</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]

    medals = ["🥇", "🥈", "🥉"]
    for rank, (count, doc, _) in enumerate(users[:10], 1):
        medal = medals[rank - 1] if rank <= 3 else f"{rank:02d}."
        username = doc.get("username")
        first_name = doc.get("first_name") or "Unknown"

        if username:
            display = f"@{escape(str(username))}"
        else:
            display = escape(str(first_name))

        lines.append(f"{medal} <b>{display}</b> — <code>×{count}</code>")

    total_owners = len(users)
    total_copies = sum(item[0] for item in users)

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        f"👥 <b>Total Owners:</b> {total_owners}",
        f"🎴 <b>Total Copies:</b> {total_copies}",
        "━━━━━━━━━━━━━━━━━━",
        "✨ <i>Ownership leaderboard</i>",
    ])

    result_text = "\n".join(lines)
    char_img = char.get("img_url")

    if char_img:
        await update.message.reply_photo(
            photo=char_img,
            caption=result_text,
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            result_text,
            parse_mode=ParseMode.HTML,
        )


application.add_handler(CommandHandler("balance", balance, block=False))
application.add_handler(CommandHandler("check",    check,    block=False))
application.add_handler(CommandHandler("daily",   daily,   block=False))
application.add_handler(CommandHandler("claim",   claim,   block=False))
application.add_handler(CommandHandler("sell",    sell,    block=False))
application.add_handler(CommandHandler("market",  market,  block=False))
application.add_handler(CommandHandler("buy",     buy,     block=False))
application.add_handler(CommandHandler("delist",  delist,  block=False))
application.add_handler(CallbackQueryHandler(market_page_cb, pattern=r"^market:\d+$", block=False))


    
