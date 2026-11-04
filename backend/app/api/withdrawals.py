from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.core.dependencies import get_telegram_user
from backend.app.services.user_service import get_user_by_telegram_id
from backend.app.services.withdrawal_service import (
    WithdrawalError,
    request_withdrawal,
    list_user_withdrawals,
)

router = APIRouter(prefix="/withdrawals", tags=["Withdrawals"])


class WithdrawalRequest(BaseModel):
    amount_usd_cents: int = Field(ge=200)
    ton_wallet_address: str = Field(min_length=48, max_length=66)


def _user(telegram_user):
    user = get_user_by_telegram_id(telegram_user["id"])

    if user is None or user["status"] != "active":
        raise HTTPException(status_code=403, detail="Active mining account required")

    return user


@router.post("")
def create_withdrawal(
    payload: WithdrawalRequest,
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _user(telegram_user)

    try:
        return request_withdrawal(
            user["id"],
            payload.amount_usd_cents,
            payload.ton_wallet_address,
        )
    except WithdrawalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me")
def my_withdrawals(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _user(telegram_user)
    return list_user_withdrawals(user["id"], limit, offset)
