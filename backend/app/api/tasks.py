from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.core.dependencies import get_telegram_user
from backend.app.services.user_service import (
    UserServiceError,
    get_user_by_telegram_id,
)
from backend.app.services.task_service import (
    TaskServiceError,
    get_task_details,
    list_active_tasks,
)
from backend.app.services.submission_service import (
    DuplicateSubmission,
    SubmissionError,
    SubmissionPermissionError,
    SubmissionValidationError,
    list_user_submissions,
    submit_task,
)


router = APIRouter(prefix="/tasks", tags=["Mining tasks"])


class TaskSubmissionRequest(BaseModel):
    evidence: str | None = Field(default=None, max_length=10000)


def _authenticated_user(telegram_user: dict) -> dict:
    try:
        user = get_user_by_telegram_id(telegram_user["id"])
    except UserServiceError as exc:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve mining account",
        ) from exc

    if not user:
        raise HTTPException(
            status_code=404,
            detail="Mining account not found. Register first.",
        )

    if user["status"] != "active":
        raise HTTPException(
            status_code=403,
            detail="Mining account is not active",
        )

    return user


@router.get("")
def browse_tasks(
    category: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    telegram_user: dict = Depends(get_telegram_user),
):
    _authenticated_user(telegram_user)

    try:
        tasks = list_active_tasks(
            category=category,
            limit=limit,
            offset=offset,
        )
        return {
            "tasks": tasks,
            "limit": limit,
            "offset": offset,
        }
    except TaskServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/me/submissions")
def my_submissions(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _authenticated_user(telegram_user)

    try:
        submissions = list_user_submissions(
            user_id=user["id"],
            limit=limit,
            offset=offset,
        )
        return {
            "submissions": submissions,
            "limit": limit,
            "offset": offset,
        }
    except SubmissionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}")
def task_details(
    task_id: int,
    telegram_user: dict = Depends(get_telegram_user),
):
    _authenticated_user(telegram_user)

    try:
        task = get_task_details(task_id)
    except TaskServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if task is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found or unavailable",
        )

    return {"task": task}


@router.post("/{task_id}/submit")
def submit_task_endpoint(
    payload: TaskSubmissionRequest,
    task_id: int,
    telegram_user: dict = Depends(get_telegram_user),
):
    user = _authenticated_user(telegram_user)

    try:
        return submit_task(
            task_id=task_id,
            user_id=user["id"],
            evidence=payload.evidence,
        )
    except DuplicateSubmission as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SubmissionPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SubmissionValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SubmissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
