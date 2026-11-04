import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]

load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    APP_NAME = "AdBot Mining Network"
    APP_VERSION = "1.0.0"

    BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
    ADBOT_OWNER_ID = os.getenv("ADBOT_OWNER_ID", "").strip()

    MINING_MINI_APP_URL = os.getenv("MINING_MINI_APP_URL", "").strip()
    MINING_SECRET_KEY = os.getenv("MINING_SECRET_KEY", "").strip()

    DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

    TON_NETWORK = os.getenv("TON_NETWORK", "testnet").strip().lower()
    TON_API_KEY = os.getenv("TON_API_KEY", "").strip()
    TON_WALLET_ADDRESS = os.getenv("TON_WALLET_ADDRESS", "").strip()

    TELEGRAM_AUTH_MAX_AGE_SECONDS = 300
    TELEGRAM_AUTH_FUTURE_TOLERANCE_SECONDS = 30

    MINIMUM_WITHDRAWAL_USD_CENTS = 200
    MIN_WITHDRAWAL_USD_CENTS = 200

    @classmethod
    def validate_telegram_auth(cls):
        if not cls.BOT_TOKEN or ":" not in cls.BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN configuration is invalid")

        token_id, separator, token_secret = cls.BOT_TOKEN.partition(":")

        if not separator or not token_id.isdigit() or not token_secret:
            raise RuntimeError("BOT_TOKEN configuration is invalid")

    @classmethod
    def validate_owner_id(cls):
        if not cls.ADBOT_OWNER_ID.isdigit():
            raise RuntimeError("ADBOT_OWNER_ID must be a numeric Telegram ID")

        if int(cls.ADBOT_OWNER_ID) <= 0:
            raise RuntimeError("ADBOT_OWNER_ID must be positive")

    @classmethod
    def validate_ton_network(cls):
        if cls.TON_NETWORK not in ("testnet", "mainnet"):
            raise RuntimeError("TON_NETWORK must be testnet or mainnet")


settings = Settings()
