"""
waifu/__main__.py — Entry point with a Telegram-file image proxy.

Important:
MongoDB stores Telegram photo file_ids. The Mini App cannot render a
file_id directly. This version resolves the file_id to a Telegram file
URL, then redirects the browser to that image URL.

This avoids downloading the image through Render, which was timing out.
"""

import asyncio
import hashlib
import hmac
import importlib
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from flask import Flask, jsonify, request, redirect, send_from_directory

from waifu import (
    ALL_MODULES, LOGGER, TOKEN, application, collection, user_collection,
    Config, OWNER_ID, sudo_users, DEV_LIST, sudo_collection,
)

_web_app = Flask(__name__)
_WEB_DIR = Path(__file__).resolve().parent / "web"

_BOT_LOOP = None


@_web_app.route("/")
def _health_check():
    return "Waifu bot is running!", 200


@_web_app.route("/shop")
def _shop_page():
    return send_from_directory(_WEB_DIR, "shop.html")


def _validate_telegram_init_data(init_data: str):
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
    return _validate_telegram_init_data(
        request.headers.get("X-Telegram-Init-Data", "")
    )


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


async def _await_any(awaitable):
    # Motor/PyMongo async operations can return Future-like awaitables rather
    # than native coroutine objects. Wrapping the awaitable guarantees that
    # run_coroutine_threadsafe always receives an actual coroutine.
    return await awaitable


def _run_on_bot_loop(awaitable):
    if _BOT_LOOP is None or _BOT_LOOP.is_closed():
        raise RuntimeError("Bot event loop is not ready.")

    future = asyncio.run_coroutine_threadsafe(
        _await_any(awaitable),
        _BOT_LOOP,
    )
    return future.result(timeout=45)


async def _fetch_shop_data(user_id: int):
    doc = await user_collection.find_one({"id": user_id})

    chars = []
    async for c in collection.find({}):
        c = dict(c)
        c.pop("_id", None)

        if c.get("img_url"):
            c["img_url"] = f"/api/shop/image/{c.get('id', '')}"

        c["price"] = _price_for_character(c)
        chars.append(c)

    return doc, chars


async def _purchase_character(user_id: int, char_id: str):
    char = await collection.find_one({"id": char_id})
    if not char:
        return None, "Character not found."

    price = _price_for_character(char)

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


@_web_app.get("/api/shop/characters")
def _shop_characters():
    tg_user = _mini_app_user()
    if not tg_user:
        return jsonify({"error": "Invalid or expired Telegram session."}), 401

    try:
        doc, chars = _run_on_bot_loop(
            _fetch_shop_data(int(tg_user["id"]))
        )
    except Exception as exc:
        LOGGER.error("Shop character API failed: %s", exc, exc_info=True)
        return jsonify({"error": "Database error."}), 500

    return jsonify({
        "balance": int((doc or {}).get("coins", 0)),
        "characters": chars,
    })


async def _telegram_file_path(file_id: str):
    """
    Resolve file_id to Telegram's CDN file_path.
    Uses a short explicit timeout and a couple of retries.
    """
    url = f"https://api.telegram.org/bot{TOKEN}/getFile"
    timeout = httpx.Timeout(12.0, connect=8.0)

    last_error = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                response = await client.get(
                    url,
                    params={"file_id": file_id},
                )
                response.raise_for_status()
                payload = response.json()

                if payload.get("ok") and payload.get("result", {}).get("file_path"):
                    return payload["result"]["file_path"]

                raise RuntimeError(f"Telegram getFile failed: {payload}")
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)

    raise last_error


@_web_app.get("/api/shop/image/<char_id>")
def _shop_image(char_id):
    """
    Resolve Telegram file_id and redirect the Mini App to Telegram's
    file CDN. Render no longer downloads the image itself, so the
    previous Render -> Telegram download timeout is avoided.
    """
    try:
        char = _run_on_bot_loop(
            collection.find_one({"id": str(char_id)})
        )

        if not char or not char.get("img_url"):
            return "", 404

        img_ref = str(char["img_url"])

        # Existing normal URLs are passed through unchanged.
        if img_ref.startswith(("http://", "https://")):
            return redirect(img_ref, code=302)

        file_path = _run_on_bot_loop(
            _telegram_file_path(img_ref)
        )

        telegram_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

        # The browser receives the redirect; Render does not download
        # the image bytes.
        return redirect(telegram_url, code=302)

    except Exception as exc:
        LOGGER.error(
            "Shop image failed for %s: %s",
            char_id,
            exc,
            exc_info=True,
        )
        return "", 404


@_web_app.post("/api/shop/buy")
def _shop_buy():
    tg_user = _mini_app_user()
    if not tg_user:
        return jsonify({"error": "Invalid or expired Telegram session."}), 401

    body = request.get_json(silent=True) or {}
    char_id = str(body.get("id", "")).strip()
    if not char_id:
        return jsonify({"error": "Character ID is required."}), 400

    try:
        result, error = _run_on_bot_loop(
            _purchase_character(int(tg_user["id"]), char_id)
        )
    except Exception as exc:
        LOGGER.error("Shop purchase failed: %s", exc, exc_info=True)
        return jsonify({"error": "Purchase failed."}), 500

    if error:
        return jsonify({"error": error}), 400

    return jsonify(result), 200


def _run_web_server():
    port = int(os.environ.get("PORT", 10000))
    _web_app.run(host="0.0.0.0", port=port, use_reloader=False)


def _start_web_server():
    threading.Thread(
        target=_run_web_server,
        daemon=True,
    ).start()


async def _migrate_indexes():
    try:
        await user_collection.drop_index("user_id_1")
    except Exception:
        pass


async def _sync_sudo_users():
    """Load sudo users from MongoDB, preserving existing SUDO_IDS on first run."""
    await sudo_collection.create_index("user_id", unique=True)

    # Seed database with the sudo IDs currently configured in the environment.
    # This makes migration safe: existing sudo users are not lost.
    env_sudos = set(Config.sudo_users)
    if env_sudos:
        await sudo_collection.bulk_write([
            __import__("pymongo").UpdateOne(
                {"user_id": int(uid)},
                {"$setOnInsert": {"user_id": int(uid)}},
                upsert=True,
            )
            for uid in env_sudos
        ])

    stored = {
        int(doc["user_id"])
        async for doc in sudo_collection.find({}, {"user_id": 1})
        if str(doc.get("user_id", "")).lstrip("-").isdigit()
    }

    sudo_users.clear()
    sudo_users.update({OWNER_ID, *stored})
    DEV_LIST.clear()
    DEV_LIST.add(OWNER_ID)

    LOGGER.info("Dynamic sudo list loaded: %d sudo user(s).", len(sudo_users) - 1)


async def _post_init(application_instance):
    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()

    from waifu.modules.inlinequery import create_indexes

    await _migrate_indexes()
    await create_indexes()
    await _sync_sudo_users()
    LOGGER.info("DB indexes ensured.")


def main():
    _start_web_server()

    LOGGER.info("Loading %d module(s)…", len(ALL_MODULES))

    for name in ALL_MODULES:
        try:
            importlib.import_module(f"waifu.modules.{name}")
            LOGGER.debug("  ✓ %s", name)
        except Exception as exc:
            LOGGER.error("  ✗ %s — %s", name, exc, exc_info=True)
            raise

    application.post_init = _post_init

    LOGGER.info("Starting bot (polling)…")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

    
