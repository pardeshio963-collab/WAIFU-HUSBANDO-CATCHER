"""
waifu/__main__.py — Entry point.
Run with: python -m waifu
"""
import hashlib
import hmac
import importlib
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl

from flask import Flask, jsonify, request, send_from_directory

from waifu import ALL_MODULES, LOGGER, TOKEN, collection, user_collection


_web_app = Flask(__name__)
_WEB_DIR = Path(__file__).resolve().parent / "web"


@_web_app.route("/")
def _health_check():
    return "Waifu bot is running!", 200


@_web_app.route("/shop")
def _shop_page():
    return send_from_directory(_WEB_DIR, "shop.html")


def _validate_telegram_init_data(init_data: str):
    """
    Validate Telegram Mini App initData.
    Returns the Telegram user dict on success, otherwise None.
    """
    if not init_data:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={pairs[key]}" for key in sorted(pairs)
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # Reject stale initData (1 hour).
        auth_date = int(pairs.get("auth_date", "0"))
        if auth_date <= 0 or time.time() - auth_date > 3600:
            return None

        user_raw = pairs.get("user")
        if not user_raw:
            return None

        return json.loads(user_raw)
    except Exception:
        return None


def _mini_app_user():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    return _validate_telegram_init_data(init_data)


def _price_for_character(char):
    custom = char.get("shop_price")
    if custom is not None:
        try:
            return max(0, int(custom))
        except (TypeError, ValueError):
            pass

    return {
        "⚪ Common": 150,
        "🟣 Rare": 250,
        "🟡 Legendary": 400,
        "🪁 Skyrise": 3999,
        "💮 Exclusive": 1000,
        "🔮 Mythical": 2500,
        "🫧 Special": 3000,
        "🌤️ Summer": 3500,
        "🧧 Limited": 5000,
    }.get(char.get("rarity"), 0)


@_web_app.get("/api/shop/characters")
def _shop_characters():
    tg_user = _mini_app_user()
    if not tg_user:
        return jsonify({"error": "Invalid or expired Telegram session."}), 401

    user_id = int(tg_user["id"])
    user = user_collection.find_one  # keeps route readable; actual async DB call below

    # Flask is synchronous while the bot uses Motor. Run the small async operation
    # in a dedicated event loop for this request.
    import asyncio

    async def fetch():
        doc = await user_collection.find_one({"id": user_id})
        cursor = collection.find({})
        chars = []
        async for c in cursor:
            c = dict(c)
            c.pop("_id", None)
            c["price"] = _price_for_character(c)
            chars.append(c)
        return doc, chars

    try:
        doc, chars = asyncio.run(fetch())
    except Exception as exc:
        LOGGER.error("Shop character API failed: %s", exc, exc_info=True)
        return jsonify({"error": "Database error."}), 500

    return jsonify({
        "balance": int((doc or {}).get("coins", 0)),
        "characters": chars,
    })


@_web_app.post("/api/shop/buy")
def _shop_buy():
    tg_user = _mini_app_user()
    if not tg_user:
        return jsonify({"error": "Invalid or expired Telegram session."}), 401

    body = request.get_json(silent=True) or {}
    char_id = str(body.get("id", "")).strip()
    if not char_id:
        return jsonify({"error": "Character ID is required."}), 400

    import asyncio

    async def purchase():
        char = await collection.find_one({"id": char_id})
        if not char:
            return None, "Character not found."

        price = _price_for_character(char)
        user_id = int(tg_user["id"])

        # Atomic balance check + deduction + harem insertion.
        result = await user_collection.update_one(
            {"id": user_id, "coins": {"$gte": price}},
            {
                "$inc": {"coins": -price},
                "$push": {"characters": char},
            },
        )

        if result.modified_count != 1:
            user = await user_collection.find_one({"id": user_id}, {"coins": 1})
            if not user:
                return None, "Your bot user profile was not found. Use /start first."
            return None, "Not enough coins."

        new_user = await user_collection.find_one({"id": user_id}, {"coins": 1})
        return {
            "name": char.get("name", char_id),
            "price": price,
            "balance": int((new_user or {}).get("coins", 0)),
        }, None

    try:
        result, error = asyncio.run(purchase())
    except Exception as exc:
        LOGGER.error("Shop purchase failed: %s", exc, exc_info=True)
        return jsonify({"error": "Purchase failed."}), 500

    if error:
        return jsonify({"error": error}), 400

    return jsonify(result), 200


def _run_web_server() -> None:
    port = int(os.environ.get("PORT", 10000))
    _web_app.run(host="0.0.0.0", port=port, use_reloader=False)


def _start_web_server() -> None:
    thread = threading.Thread(target=_run_web_server, daemon=True)
    thread.start()
    LOGGER.info("Web server started on port %s.", os.environ.get("PORT", "10000"))


async def _migrate_indexes() -> None:
    from waifu import user_collection
    try:
        await user_collection.drop_index("user_id_1")
        LOGGER.info("Migration: dropped stale index users.user_id_1")
    except Exception:
        pass


async def _post_init(application) -> None:
    from waifu.modules.inlinequery import create_indexes
    await _migrate_indexes()
    await create_indexes()
    LOGGER.info("DB indexes ensured.")


def main() -> None:
    _start_web_server()

    LOGGER.info("Loading %d module(s)…", len(ALL_MODULES))
    for name in ALL_MODULES:
        try:
            importlib.import_module(f"waifu.modules.{name}")
            LOGGER.debug("  ✓ %s", name)
        except Exception as exc:
            LOGGER.error("  ✗ %s — %s", name, exc, exc_info=True)
            raise
    LOGGER.info("All modules loaded.")

    from waifu import application
    application.post_init = _post_init

    LOGGER.info("Starting bot (polling)…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
    
