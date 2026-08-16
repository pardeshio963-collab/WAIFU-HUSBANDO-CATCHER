import re
import time
from html import escape
from pymongo import ASCENDING
from cachetools import TTLCache
from telegram import InlineQueryResultCachedPhoto, InlineQueryResultPhoto, Update
from telegram.ext import CallbackContext, InlineQueryHandler
from waifu import application, collection, db, user_collection

_all_cache  = TTLCache(maxsize=1,     ttl=3600)
_user_cache = TTLCache(maxsize=10000, ttl=60)
_url_cache  = TTLCache(maxsize=5000,  ttl=3600)   # file_id → HTTP URL cache
_PAGE = 50


async def create_indexes() -> None:
    await db.anime_characters.create_index([("id",    ASCENDING)])
    await db.anime_characters.create_index([("anime", ASCENDING)])
    await db.users.create_index([("characters.id", ASCENDING)])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _resolve_url(img: str, bot) -> str | None:
    """
    Return a public HTTPS URL for the image.
    - If img already starts with http  → return as-is.
    - If img looks like a Telegram file_id → ask Telegram for the download URL.
      Results are cached 1 hour to avoid hammering the API.
    """
    if not img:
        return None
    if img.startswith("http"):
        return img
    if img in _url_cache:
        return _url_cache[img]
    try:
        file = await bot.get_file(img)
        url  = file.file_path          # always https://api.telegram.org/…
        _url_cache[img] = url
        return url
    except Exception:
        return None


async def _batch_global(ids: list) -> dict:
    pipeline = [
        {"$unwind": "$characters"},
        {"$match":  {"characters.id": {"$in": ids}}},
        {"$group":  {"_id": "$characters.id", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in user_collection.aggregate(pipeline)}


async def _batch_anime(animes: list) -> dict:
    pipeline = [
        {"$match": {"anime": {"$in": animes}}},
        {"$group": {"_id": "$anime", "n": {"$sum": 1}}},
    ]
    return {d["_id"]: d["n"] async for d in collection.aggregate(pipeline)}


# ── Main handler ──────────────────────────────────────────────────────────────

async def inlinequery(update: Update, context: CallbackContext) -> None:
    raw    = update.inline_query.query.strip()
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    user: dict | None = None
    chars: list[dict] = []

    if raw.startswith("collection."):
        # ── User's personal collection ──────────────────────────────────────
        parts    = raw.split(" ", 1)
        uid_part = parts[0].split(".")[1]
        search   = parts[1].strip() if len(parts) > 1 else ""

        if uid_part.isdigit():
            uid = int(uid_part)
            key = uid_part

            # FIX 1: only use cache when the stored value is not None.
            # Previously None was cached, so new players always got empty
            # results for 60 seconds even after guessing their first character.
            if key in _user_cache and _user_cache[key] is not None:
                user = _user_cache[key]
            else:
                user = await user_collection.find_one({"id": uid})
                if user:
                    _user_cache[key] = user   # only cache real documents

            if user:
                deduped = list({c["id"]: c for c in user.get("characters", [])}.values())
                if search:
                    # Python-side filter on the already-fetched list — re is fine here
                    pat     = re.compile(re.escape(search), re.IGNORECASE)
                    deduped = [
                        c for c in deduped
                        if pat.search(c.get("name", "")) or pat.search(c.get("anime", ""))
                    ]
                chars = deduped

    else:
        # ── Global catalogue search ─────────────────────────────────────────
        if raw:
            # FIX 2: use MongoDB $regex operator instead of a compiled Python
            # re object. Motor's async driver doesn't reliably convert
            # re.compile() objects into BSON regex across all driver versions.
            query = {
                "$or": [
                    {"name":  {"$regex": re.escape(raw), "$options": "i"}},
                    {"anime": {"$regex": re.escape(raw), "$options": "i"}},
                ]
            }
            chars = await collection.find(query).to_list(5000)
        else:
            if "all" in _all_cache:
                chars = _all_cache["all"]
            else:
                chars = await collection.find({}).to_list(5000)
                _all_cache["all"] = chars

    # ── Paginate ──────────────────────────────────────────────────────────────
    page_chars  = chars[offset:offset + _PAGE]
    next_offset = str(offset + len(page_chars)) if len(page_chars) == _PAGE else ""

    if not page_chars:
        await update.inline_query.answer([], cache_time=5)
        return

    # ── Batch DB stats (2 queries total for the whole page) ───────────────────
    ids     = [c["id"]    for c in page_chars]
    animes  = list({c["anime"] for c in page_chars})
    g_count = await _batch_global(ids)
    a_total = await _batch_anime(animes)

    # ── Build results ─────────────────────────────────────────────────────────
    results = []
    for c in page_chars:
        name  = escape(c.get("name",  "Unknown"))
        anime = escape(c.get("anime", "Unknown"))

        # FIX 3: InlineQueryResultPhoto requires a real HTTPS URL.
        # Characters uploaded via /uploadchar store a Telegram file_id, not
        # a URL. Resolve it via bot.get_file() and cache the result.
        img = c.get("img_url", "")
        if not img:
            continue

        # Characters uploaded by /uploadchar store a Telegram file_id.
        # Use InlineQueryResultCachedPhoto for those instead of converting
        # the file_id to an API download URL. This is much more reliable
        # for Telegram inline results.
        is_file_id = not str(img).startswith(("http://", "https://"))
        photo_url = None if is_file_id else str(img)

        if not is_file_id:
            if not photo_url:
                continue

        if user and raw.startswith("collection."):
            u_cnt = sum(1 for x in user.get("characters", []) if x["id"] == c["id"])
            u_an  = sum(1 for x in user.get("characters", []) if x["anime"] == c["anime"])
            db_an = a_total.get(c["anime"], "?")
            uid   = user.get("id", "")
            uname = escape(user.get("first_name", str(uid)))
            cap   = (
                f"<b><a href='tg://user?id={uid}'>{uname}</a>'s Character</b>\n\n"
                f"🌸 <b>{name}</b> (×{u_cnt})\n"
                f"📺 <b>{anime}</b> ({u_an}/{db_an})\n"
                f"💎 {c.get('rarity', '')}\n"
                f"🆔 {c['id']}"
            )
        else:
            gc  = g_count.get(c["id"], 0)
            cap = (
                f"🌸 <b>{name}</b>\n\n"
                f"📺 {anime}\n"
                f"💎 {c.get('rarity', '')}\n"
                f"🆔 {c['id']}\n\n"
                f"Guessed globally <b>{gc}</b> time{'s' if gc != 1 else ''}."
            )

        result_id = f"{c['id']}_{time.time_ns()}"

        if is_file_id:
            results.append(InlineQueryResultCachedPhoto(
                id=result_id,
                photo_file_id=str(img),
                caption=cap,
                parse_mode="HTML",
            ))
        else:
            results.append(InlineQueryResultPhoto(
                id=result_id,
                photo_url=photo_url,
                thumbnail_url=photo_url,
                caption=cap,
                parse_mode="HTML",
            ))

    await update.inline_query.answer(results, next_offset=next_offset, cache_time=5)


application.add_handler(InlineQueryHandler(inlinequery, block=False))
