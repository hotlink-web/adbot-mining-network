from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from backend.app.core.dependencies import get_owner_user
from backend.app.services.submission_service import (
    SubmissionError,
    SubmissionNotFound,
    SubmissionPermissionError,
    SubmissionValidationError,
    review_submission,
)


router = APIRouter(
    prefix="/admin/submissions",
    tags=["Admin submissions"],
)


class SubmissionReviewRequest(BaseModel):
    decision: Literal["approved", "rejected", "under_review"]
    review_note: str | None = Field(default=None, max_length=2000)


@router.post("/{submission_id}/review")
def review_submission_endpoint(
    payload: SubmissionReviewRequest,
    submission_id: int = Path(gt=0),
    owner: dict = Depends(get_owner_user),
):
    try:
        return review_submission(
            submission_id=submission_id,
            reviewer_user_id=owner["id"],
            decision=payload.decision,
            review_note=payload.review_note,
        )

    except SubmissionNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail="Submission not found",
        ) from exc

    except SubmissionPermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail="Submission review not permitted",
        ) from exc

    except SubmissionValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except SubmissionError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
