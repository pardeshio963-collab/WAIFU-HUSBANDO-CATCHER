import os
from dotenv import load_dotenv

load_dotenv()


def _req(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if not v:
        raise RuntimeError(f"Required env var '{key}' not set. Copy .env.example → .env")
    return v


def _int_list(key: str) -> list[int]:
    return [int(x.strip()) for x in os.environ.get(key, "").split(",")
            if x.strip().lstrip("-").isdigit()]


class Config:
    TOKEN:            str       = _req("BOT_TOKEN")
    BOT_USERNAME:     str       = _req("BOT_USERNAME")
    OWNER_ID:         int       = int(_req("OWNER_ID"))
    sudo_users:       list[int] = _int_list("SUDO_IDS")
    GROUP_ID:         int       = int(_req("GROUP_ID"))
    CHARA_CHANNEL_ID: int       = int(_req("CHARA_CHANNEL_ID"))
    mongo_url:        str       = _req("MONGO_URI")
    DB_NAME:          str       = os.environ.get("DB_NAME", "waifu_bot")
    SUPPORT_CHAT:     str       = os.environ.get("SUPPORT_CHAT", "")
    UPDATE_CHAT:      str       = os.environ.get("UPDATE_CHAT",  "")
    PHOTO_URL:        list[str] = [u.strip() for u in
                                   os.environ.get("PHOTO_URLS", "").split(",")
                                   if u.strip().startswith("http")]
    DROP_INTERVAL_MIN:    int   = int(os.environ.get("DROP_INTERVAL_MINUTES", "15"))
    DEFAULT_MSG_FREQUENCY: int  = int(os.environ.get("DEFAULT_MSG_FREQUENCY", "100"))

    # Economy
    DAILY_COINS:     int = 200
    DUEL_WIN_COINS:  int = 150
    DUEL_LOSE_COINS: int = 30

    # Rarity map
    RARITY_MAP: dict[int, str] = {
        1: "⚪ Legendary",
        2: "🟣 Rare",
        3: "💮 Special",
        4: "🔵 Limited",
        5: "🔴 Exclusive",
        6: "🌌 Celestial",
        7: "🟠 Mythic",
        8: "💎 Luxury Edition",
    }

    # Edition map
    EDITION_MAP: dict[int, str] = {
        1: "🎃 Halloween",
        2: "💕 Valentine",
        3: "🩺 Doctor",
        4: "🐞 Bug",
        5: "🧘 Monk",
        6: "🏀 Basketball",
        7: "👶 Chibi",
        8: "👘 Kimono",
        9: "☕ Coffee",
        10: "🌈 Holi",
        11: "🧜 Mermaid",
        12: "🥻 Saree",
        13: "🫥 Abses",
        14: "🎀 Maid",
        15: "🎵 Music",
        16: "❄️ Winter",
        17: "🪔 Diwali",
        18: "🎮 Game",
        19: "🎄 Xmas",
        20: "☀️ Summer",
    }

    @classmethod
    def all_sudo(cls) -> set[int]:
        return {cls.OWNER_ID, *cls.sudo_users}
        
