"""
Waifu Nexus Character Shop module.
"""
import os
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, sudo_users

DEFAULT_PRICES = {
    "⚪ Common": 150,
    "🟣 Rare": 250,
    "🟡 Legendary": 400,
    "🪁 Skyrise": 3999,
    "💮 Exclusive": 1000,
    "🔮 Mythical": 2500,
    "🫧 Special": 3000,
    "🌤️ Summer": 3500,
    "🧧 Limited": 5000,
}

SHOP_URL = os.environ.get(
    "SHOP_URL",
    "https://waifu-husbando-catcher-glwo.onrender.com/shop",
)


def price_for_character(char: dict) -> int:
    custom = char.get("shop_price")
    if custom is not None:
        try:
            return max(0, int(custom))
        except (TypeError, ValueError):
            pass
    return DEFAULT_PRICES.get(char.get("rarity"), 0)


async def setprice(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in sudo_users:
        await update.message.reply_text("❌ Sudo only.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Usage:\n"
            "<code>/setprice 0026 25000</code>\n"
            "<code>/setprice 0026 reset</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    char_id = context.args[0].strip()
    raw_price = context.args[1].strip().lower()

    char = await collection.find_one({"id": char_id})
    if not char:
        await update.message.reply_text(
            f"❌ Character <code>{escape(char_id)}</code> not found.",
            parse_mode=ParseMode.HTML,
        )
        return

    if raw_price == "reset":
        await collection.update_one({"id": char_id}, {"$unset": {"shop_price": ""}})
        default = DEFAULT_PRICES.get(char.get("rarity"), 0)
        await update.message.reply_text(
            f"✅ <b>{escape(str(char.get('name', char_id)))}</b>\n"
            f"Custom price removed.\n"
            f"💰 Default price: <b>{default:,}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        price = int(raw_price)
    except ValueError:
        await update.message.reply_text(
            "❌ Price must be a whole number or <code>reset</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    if price < 0:
        await update.message.reply_text("❌ Price cannot be negative.")
        return

    await collection.update_one(
        {"id": char_id},
        {"$set": {"shop_price": price}},
    )

    await update.message.reply_text(
        f"✅ <b>{escape(str(char.get('name', char_id)))}</b>\n"
        f"🆔 <code>{escape(char_id)}</code>\n"
        f"💰 Shop price: <b>{price:,}</b>",
        parse_mode=ParseMode.HTML,
    )


async def shop(update: Update, context: CallbackContext) -> None:
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🛒 OPEN CHARACTER SHOP",
            web_app=WebAppInfo(url=SHOP_URL),
        )
    ]])
    await update.message.reply_text(
        "🖤 <b>WAIFU NEXUS</b>\n"
        "❤️ <b>Character Shop</b>\n\n"
        "Browse characters, check prices and buy directly.",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


application.add_handler(CommandHandler("shop", shop, block=False))
application.add_handler(CommandHandler("setprice", setprice, block=False))
