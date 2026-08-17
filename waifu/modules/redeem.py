"""
waifu/modules/redeem.py
Separate coin and character redeem-code systems.

Admin:
  /gencoincode CODE AMOUNT
  /gencharcode CODE CHARACTER_ID

User:
  /redeemcoin CODE
  /redeemchar CODE
"""

from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, user_collection, collection, db
from waifu.config import Config


redeem_collection = db["redeem_codes"]


def _is_admin(user_id: int) -> bool:
    return user_id in Config.all_sudo()


def _normalize_code(value: str) -> str:
    return value.strip().upper()


async def gen_coin_code(update: Update, context: CallbackContext) -> None:
    """Admin: /gencoincode CODE AMOUNT"""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: <code>/gencoincode CODE AMOUNT</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = _normalize_code(context.args[0])
    amount_text = context.args[1]

    if not code:
        await update.message.reply_text("❌ Invalid code.")
        return

    if not amount_text.isdigit() or int(amount_text) <= 0:
        await update.message.reply_text("❌ Coin amount must be a positive number.")
        return

    amount = int(amount_text)

    exists = await redeem_collection.find_one({"code": code})
    if exists:
        await update.message.reply_text("❌ That redeem code already exists.")
        return

    await redeem_collection.insert_one({
        "code": code,
        "type": "coins",
        "amount": amount,
        "redeemed_by": [],
    })

    await update.message.reply_text(
        f"✅ <b>Coin redeem code created!</b>\n\n"
        f"Code: <code>{escape(code)}</code>\n"
        f"Reward: <b>{amount:,} 🪙</b>",
        parse_mode=ParseMode.HTML,
    )


async def gen_char_code(update: Update, context: CallbackContext) -> None:
    """Admin: /gencharcode CODE CHARACTER_ID"""
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage: <code>/gencharcode CODE CHARACTER_ID</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = _normalize_code(context.args[0])
    char_id = context.args[1].strip()

    if not code:
        await update.message.reply_text("❌ Invalid code.")
        return

    if not char_id:
        await update.message.reply_text("❌ Invalid character ID.")
        return

    exists = await redeem_collection.find_one({"code": code})
    if exists:
        await update.message.reply_text("❌ That redeem code already exists.")
        return

    character = await collection.find_one({"id": char_id})
    if not character:
        await update.message.reply_text(
            f"❌ Character ID <code>{escape(char_id)}</code> was not found.",
            parse_mode=ParseMode.HTML,
        )
        return

    await redeem_collection.insert_one({
        "code": code,
        "type": "character",
        "character_id": char_id,
        "redeemed_by": [],
    })

    await update.message.reply_text(
        f"✅ <b>Character redeem code created!</b>\n\n"
        f"Code: <code>{escape(code)}</code>\n"
        f"Character: <b>{escape(str(character.get('name', 'Unknown')))}</b>\n"
        f"ID: <code>{escape(char_id)}</code>",
        parse_mode=ParseMode.HTML,
    )


async def redeem_coin(update: Update, context: CallbackContext) -> None:
    """User: /redeemcoin CODE"""
    user = update.effective_user

    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/redeemcoin CODE</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = _normalize_code(context.args[0])
    redeem = await redeem_collection.find_one({"code": code, "type": "coins"})

    if not redeem:
        await update.message.reply_text("❌ Invalid coin redeem code.")
        return

    # One redemption per user. $addToSet prevents duplicate user IDs.
    result = await redeem_collection.update_one(
        {"_id": redeem["_id"], "redeemed_by": {"$ne": user.id}},
        {"$addToSet": {"redeemed_by": user.id}},
    )

    if result.modified_count == 0:
        await update.message.reply_text("❌ You have already redeemed this code.")
        return

    amount = int(redeem["amount"])

    await user_collection.update_one(
        {"id": user.id},
        {
            "$inc": {"coins": amount},
            "$set": {
                "username": user.username,
                "first_name": user.first_name,
            },
            "$setOnInsert": {
                "characters": [],
                "xp": 0,
                "wins": 0,
                "total_guesses": 0,
                "favorites": [],
            },
        },
        upsert=True,
    )

    await update.message.reply_text(
        f"🎁 <b>Redeemed successfully!</b>\n\n"
        f"You received <b>{amount:,} 🪙</b>.",
        parse_mode=ParseMode.HTML,
    )


async def redeem_char(update: Update, context: CallbackContext) -> None:
    """User: /redeemchar CODE"""
    user = update.effective_user

    if len(context.args) != 1:
        await update.message.reply_text(
            "Usage: <code>/redeemchar CODE</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    code = _normalize_code(context.args[0])
    redeem = await redeem_collection.find_one(
        {"code": code, "type": "character"}
    )

    if not redeem:
        await update.message.reply_text("❌ Invalid character redeem code.")
        return

    character = await collection.find_one({"id": redeem["character_id"]})
    if not character:
        await update.message.reply_text(
            "❌ The character attached to this code no longer exists."
        )
        return

    # Claim the code first, atomically, so the same user cannot redeem it twice.
    result = await redeem_collection.update_one(
        {"_id": redeem["_id"], "redeemed_by": {"$ne": user.id}},
        {"$addToSet": {"redeemed_by": user.id}},
    )

    if result.modified_count == 0:
        await update.message.reply_text("❌ You have already redeemed this code.")
        return

    await user_collection.update_one(
        {"id": user.id},
        {
            "$push": {"characters": character},
            "$set": {
                "username": user.username,
                "first_name": user.first_name,
            },
            "$setOnInsert": {
                "coins": 0,
                "xp": 0,
                "wins": 0,
                "total_guesses": 0,
                "favorites": [],
            },
        },
        upsert=True,
    )

    await update.message.reply_text(
        f"🎉 <b>Character redeemed!</b>\n\n"
        f"🌸 <b>{escape(str(character.get('name', 'Unknown')))}</b>\n"
        f"📺 {escape(str(character.get('anime', 'Unknown')))}\n"
        f"💎 {escape(str(character.get('rarity', 'Unknown')))}\n\n"
        f"Added to your harem! ✨",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(
    CommandHandler("gencoincode", gen_coin_code, block=False)
)
application.add_handler(
    CommandHandler("gencharcode", gen_char_code, block=False)
)
application.add_handler(
    CommandHandler("redeemcoin", redeem_coin, block=False)
)
application.add_handler(
    CommandHandler("redeemchar", redeem_char, block=False)
)
