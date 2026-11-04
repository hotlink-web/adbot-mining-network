from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from backend.app.core.dependencies import get_owner_user
from backend.app.services.settlement_service import (
    SettlementError,
    record_sponsor_payment,
    confirm_sponsor_payment,
)

router = APIRouter(
    prefix="/admin/settlements",
    tags=["Admin settlements"],
)


class SponsorPaymentRequest(BaseModel):
    sponsor_id: int = Field(gt=0)
    external_reference: str = Field(min_length=1, max_length=200)
    gross_amount: int = Field(gt=0)
    fees: int = Field(default=0, ge=0)
    currency: Literal["USD", "TON"] = "USD"
    evidence: str | None = Field(default=None, max_length=2000)


class SettlementAllocation(BaseModel):
    submission_id: int = Field(gt=0)
    amount: int = Field(gt=0)
    provider_fee: int = Field(default=0, ge=0)


class ConfirmSettlementRequest(BaseModel):
    allocations: list[SettlementAllocation] = Field(
        min_length=1,
        max_length=500,
    )


@router.post("")
def record_sponsor_payment_endpoint(
    payload: SponsorPaymentRequest,
    owner: dict = Depends(get_owner_user),
):
    try:
        return record_sponsor_payment(
            sponsor_id=payload.sponsor_id,
            external_reference=payload.external_reference,
            gross_amount=payload.gross_amount,
            fees=payload.fees,
            currency=payload.currency,
            administrator_id=owner["id"],
            evidence=payload.evidence,
        )
    except SettlementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{settlement_id}/confirm")
def confirm_sponsor_payment_endpoint(
    payload: ConfirmSettlementRequest,
    settlement_id: int = Path(gt=0),
    owner: dict = Depends(get_owner_user),
):
    try:
        return confirm_sponsor_payment(
            settlement_id=settlement_id,
            administrator_id=owner["id"],
            allocations=[
                item.dict() for item in payload.allocations
            ],
        )
    except SettlementError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
