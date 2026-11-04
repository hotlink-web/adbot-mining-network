from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.core.dependencies import get_telegram_user
from backend.app.services.user_service import (
    UserServiceError,
    get_referral_summary,
    get_user_by_telegram_id,
    register_or_get_user,
)
from backend.app.services.wallet_service import (
    WalletServiceError,
    get_wallet_summary,
    get_wallet_transactions,
)
from backend.app.services.withdrawal_service import list_user_withdrawals
from backend.app.services.submission_service import (
    SubmissionError,
    list_user_submissions,
)


router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


class RegistrationRequest(BaseModel):
    referral_code: str | None = Field(
        default=None,
        max_length=32,
    )


def _authenticated_user(telegram_user):
    user = get_user_by_telegram_id(telegram_user["id"])

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Mining account not registered",
        )

    if user["status"] != "active":
        raise HTTPException(
            status_code=403,
            detail="Mining account is not active",
        )

    return user


@router.post("/register")
def register_user(
    payload: RegistrationRequest,
    telegram_user: dict = Depends(get_telegram_user),
):
    try:
        return register_or_get_user(
            telegram_user,
            referral_code=payload.referral_code,
        )

    except UserServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/me")
def get_my_account(
    telegram_user: dict = Depends(get_telegram_user),
):
    return _authenticated_user(telegram_user)


@router.get("/me/referrals")
def get_my_referrals(
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _authenticated_user(telegram_user)

    try:
        return get_referral_summary(user["id"])

    except UserServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/me/wallet")
def get_my_wallet(
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _authenticated_user(telegram_user)

    try:
        return get_wallet_summary(user["id"])

    except WalletServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/me/submissions")
def get_my_submissions(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _authenticated_user(telegram_user)

    try:
        return list_user_submissions(
            user["id"],
            limit=limit,
            offset=offset,
        )

    except SubmissionError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc


@router.get("/me/wallet/transactions")
def get_my_wallet_transactions(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _authenticated_user(telegram_user)
    try:
        return get_wallet_transactions(user["id"], limit, offset)
    except WalletServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me/withdrawals")
def get_my_withdrawals(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _authenticated_user(telegram_user)
    return list_user_withdrawals(user["id"], limit, offset)
