import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from backend.app.core.config import settings


class TelegramAuthError(Exception):
    pass


def validate_init_data(init_data, now=None):
    """
    Verify Telegram Mini App initData.

    Returns the verified Telegram user object.
    Never trust user IDs supplied outside verified initData.
    """

    if not isinstance(init_data, str) or not init_data:
        raise TelegramAuthError("Telegram authentication data is missing")

    if len(init_data) > 16384:
        raise TelegramAuthError("Telegram authentication data is too large")

    settings.validate_telegram_auth()

    try:
        pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=100,
        )
    except ValueError as exc:
        raise TelegramAuthError("Malformed Telegram authentication data") from exc

    values = {}

    for key, value in pairs:
        if key in values:
            raise TelegramAuthError("Duplicate authentication field")

        values[key] = value

    received_hash = values.pop("hash", None)

    if not received_hash or len(received_hash) != 64:
        raise TelegramAuthError("Telegram authentication hash is missing")

    if any(char not in "0123456789abcdefABCDEF" for char in received_hash):
        raise TelegramAuthError("Telegram authentication hash is invalid")

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(values.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        settings.BOT_TOKEN.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        calculated_hash,
        received_hash.lower(),
    ):
        raise TelegramAuthError("Telegram authentication signature is invalid")

    auth_date_value = values.get("auth_date")

    if not auth_date_value or not auth_date_value.isdigit():
        raise TelegramAuthError("Telegram authentication date is invalid")

    auth_date = int(auth_date_value)
    current_time = int(time.time()) if now is None else int(now)

    if auth_date > current_time + settings.TELEGRAM_AUTH_FUTURE_TOLERANCE_SECONDS:
        raise TelegramAuthError("Telegram authentication date is in the future")

    if current_time - auth_date > settings.TELEGRAM_AUTH_MAX_AGE_SECONDS:
        raise TelegramAuthError("Telegram authentication has expired")

    user_json = values.get("user")

    if not user_json:
        raise TelegramAuthError("Telegram user information is missing")

    try:
        user = json.loads(user_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise TelegramAuthError("Telegram user information is invalid") from exc

    if not isinstance(user, dict):
        raise TelegramAuthError("Telegram user information is invalid")

    telegram_id = user.get("id")

    if (
        isinstance(telegram_id, bool)
        or not isinstance(telegram_id, int)
        or telegram_id <= 0
    ):
        raise TelegramAuthError("Telegram user ID is invalid")

    return user
