from fastapi import Depends, Header, HTTPException

from backend.app.core.telegram_auth import (
    TelegramAuthError,
    validate_init_data,
)


def get_telegram_user(
    telegram_init_data: str = Header(
        default="",
        alias="X-Telegram-Init-Data",
    ),
):
    try:
        return validate_init_data(telegram_init_data)

    except TelegramAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail="Telegram authentication failed",
        ) from exc


def get_owner_user(telegram_user=Depends(get_telegram_user)):

    from backend.app.core.config import settings
    from backend.app.services.user_service import get_user_by_telegram_id

    try:
        settings.validate_owner_id()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Owner authentication is not configured",
        ) from exc

    if telegram_user["id"] != int(settings.ADBOT_OWNER_ID):
        raise HTTPException(
            status_code=403,
            detail="Owner access required",
        )

    owner = get_user_by_telegram_id(telegram_user["id"])

    if owner is None:
        raise HTTPException(
            status_code=403,
            detail="Register your mining account before using owner controls",
        )

    if owner["status"] != "active":
        raise HTTPException(
            status_code=403,
            detail="Owner mining account is not active",
        )

    return owner
