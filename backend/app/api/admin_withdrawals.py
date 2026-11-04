from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from backend.app.core.dependencies import get_owner_user
from backend.app.services.withdrawal_service import (
    WithdrawalError,
    list_admin_withdrawals,
    mark_withdrawal_paid,
    release_withdrawal,
)

router = APIRouter(prefix="/admin/withdrawals", tags=["Admin withdrawals"])


class PaidRequest(BaseModel):
    ton_amount_nanoton: int = Field(gt=0)
    exchange_rate_usd_cents_per_ton: int = Field(gt=0)
    payout_reference: str = Field(min_length=1, max_length=200)


class ReleaseRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    outcome: Literal["rejected", "failed"] = "rejected"


@router.get("")
def admin_list_withdrawals(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    owner: dict = Depends(get_owner_user),
):
    return list_admin_withdrawals(status, limit, offset)


@router.post("/{withdrawal_id}/paid")
def admin_mark_paid(
    payload: PaidRequest,
    withdrawal_id: int = Path(gt=0),
    owner: dict = Depends(get_owner_user),
):
    try:
        return mark_withdrawal_paid(
            withdrawal_id,
            owner["id"],
            payload.ton_amount_nanoton,
            payload.exchange_rate_usd_cents_per_ton,
            payload.payout_reference,
        )
    except WithdrawalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{withdrawal_id}/release")
def admin_release(
    payload: ReleaseRequest,
    withdrawal_id: int = Path(gt=0),
    owner: dict = Depends(get_owner_user),
):
    try:
        return release_withdrawal(
            withdrawal_id,
            owner["id"],
            payload.reason,
            failed=(payload.outcome == "failed"),
        )
    except WithdrawalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
