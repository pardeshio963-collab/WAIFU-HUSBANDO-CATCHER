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
        1: "⚪ Common",
        2: "🟣 Rare",
        3: "🟡 Legendary",
        4: "🪁 Skyrise",
        5: "💮 Exclusive",
        6: "🔮 Mythical",
        7: "🫧 Special",
        8: "🌤️ Summer",
        9: "🧧 Limited",
    }

    # Edition map
    EDITION_MAP: dict[int, str] = {
        1: "💞 Valentine",
        2: "🎃 Halloween",
        3: "❄️ Winter",
        4: "🏖 Summer",
        5: "🪔 Diwali",
        6: "🎸 Music",
        7: "🏀 Basketball",
        8: "🧹 Maid",
        9: "🎮 Game",
        10: "🎄 Xmas",
        11: "🩺 Nurse",
        12: "💍 Bride",
        13: "🎒 Bag",
        14: "🚓 Police",
        15: "🐰 Bunny",
        16: "🀄️ Abess",
        17: "👘 Kimono",
        18: "👶 Chibi",
        19: "🎊 Cheerleaders",
        20: "🥻 Saree",
        21: "🎾 Tennis",
        22: "🥤 Coffee",
        23: "🏋️‍♂️ Gym",
        24: "🔫 Holi",
        25: "🧜‍♀️ Mermaid",
        26: "🌦 Monsoon",
        27: "🎂 Birthday",
        28: "☀️ Summer",
    }

    @classmethod
    def all_sudo(cls) -> set[int]:
        return {cls.OWNER_ID, *cls.sudo_users}

