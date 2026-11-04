import hashlib
import hmac
import json
import sqlite3

from backend.app.database.mining_db import connect


class SubmissionError(Exception):
    pass


class SubmissionNotFound(SubmissionError):
    pass


class DuplicateSubmission(SubmissionError):
    pass


class SubmissionValidationError(SubmissionError):
    pass


class SubmissionPermissionError(SubmissionError):
    pass


class ProviderAuthenticationError(SubmissionError):
    pass


def _positive_id(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SubmissionValidationError(f"{field} must be a positive integer")
    return value


def _text(value, field, maximum, required=False):
    if value is None:
        if required:
            raise SubmissionValidationError(f"{field} is required")
        return None

    if not isinstance(value, str):
        raise SubmissionValidationError(f"{field} must be text")

    value = value.strip()

    if required and not value:
        raise SubmissionValidationError(f"{field} is required")

    if len(value) > maximum:
        raise SubmissionValidationError(
            f"{field} must not exceed {maximum} characters"
        )

    return value or None


def hash_provider_secret(secret):
    """Return the SHA-256 hash to store in callback_secret_hash."""
    if not isinstance(secret, str) or len(secret) < 32:
        raise SubmissionValidationError(
            "Provider secret must contain at least 32 characters"
        )

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _restore_task_budget(db, task_id, reward_amount):
    result = db.execute(
        """
        UPDATE mining_tasks
        SET remaining_budget = remaining_budget + ?
        WHERE id = ?
          AND remaining_budget + ? <= task_budget
        """,
        (reward_amount, task_id, reward_amount),
    )

    if result.rowcount != 1:
        raise SubmissionError("Could not safely restore task budget")


def submit_task(task_id, user_id, evidence=None):
    task_id = _positive_id(task_id, "task_id")
    user_id = _positive_id(user_id, "user_id")
    evidence = _text(evidence, "evidence", 10000)

    db = connect()

    try:
        db.execute("BEGIN IMMEDIATE")

        user = db.execute(
            """
            SELECT id, status
            FROM mining_users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if not user or user["status"] != "active":
            raise SubmissionPermissionError("User is not active")

        task = db.execute(
            """
            SELECT
                t.id,
                t.reward_amount,
                t.reward_currency,
                t.remaining_budget,
                t.verification_type,
                t.status,
                t.provider_id,
                t.sponsor_id
            FROM mining_tasks AS t
            LEFT JOIN mining_sponsors AS s
                ON s.id = t.sponsor_id
            LEFT JOIN mining_providers AS p
                ON p.id = t.provider_id
            WHERE t.id = ?
              AND t.status = 'active'
              AND (t.starts_at IS NULL OR datetime(t.starts_at) <= CURRENT_TIMESTAMP)
              AND (t.expires_at IS NULL OR datetime(t.expires_at) > CURRENT_TIMESTAMP)
              AND (t.sponsor_id IS NULL OR s.status = 'active')
              AND (t.provider_id IS NULL OR p.status = 'active')
            """,
            (task_id,),
        ).fetchone()

        if not task:
            raise SubmissionValidationError(
                "Task is unavailable, expired, or inactive"
            )

        if task["remaining_budget"] < task["reward_amount"]:
            raise SubmissionValidationError("Task has insufficient remaining budget")

        existing = db.execute(
            """
            SELECT id
            FROM mining_submissions
            WHERE task_id = ? AND user_id = ?
            """,
            (task_id, user_id),
        ).fetchone()

        if existing:
            raise DuplicateSubmission("You have already submitted this task")

        result = db.execute(
            """
            UPDATE mining_tasks
            SET remaining_budget = remaining_budget - ?
            WHERE id = ?
              AND remaining_budget >= ?
              AND status = 'active'
            """,
            (
                task["reward_amount"],
                task_id,
                task["reward_amount"],
            ),
        )

        if result.rowcount != 1:
            raise SubmissionError("Task budget could not be reserved")

        cursor = db.execute(
            """
            INSERT INTO mining_submissions (
                task_id,
                user_id,
                evidence,
                status
            )
            VALUES (?, ?, ?, 'pending')
            """,
            (task_id, user_id, evidence),
        )

        submission_id = cursor.lastrowid

        if task["verification_type"] == "automatic":
            db.execute(
                """
                INSERT INTO mining_verifications (
                    submission_id,
                    verification_status
                )
                VALUES (?, 'pending')
                """,
                (submission_id,),
            )

        db.commit()

        return {
            "submission_id": submission_id,
            "task_id": task_id,
            "user_id": user_id,
            "status": "pending",
            "verification_type": task["verification_type"],
            "reward_amount": task["reward_amount"],
            "reward_currency": task["reward_currency"],
            "budget_reserved": True,
            "wallet_credited": False,
        }

    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise DuplicateSubmission(
            "A submission already exists or a database constraint was violated"
        ) from exc

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def review_submission(
    submission_id,
    reviewer_user_id,
    decision,
    review_note=None,
):
    submission_id = _positive_id(submission_id, "submission_id")
    reviewer_user_id = _positive_id(reviewer_user_id, "reviewer_user_id")

    decision = _text(decision, "decision", 20, required=True)
    review_note = _text(review_note, "review_note", 2000)

    if decision not in ("approved", "rejected", "under_review"):
        raise SubmissionValidationError("Invalid review decision")

    db = connect()

    try:
        db.execute("BEGIN IMMEDIATE")

        reviewer = db.execute(
            """
            SELECT id, status
            FROM mining_users
            WHERE id = ?
            """,
            (reviewer_user_id,),
        ).fetchone()

        if not reviewer or reviewer["status"] != "active":
            raise SubmissionPermissionError(
                "Reviewer must be an active mining user"
            )

        submission = db.execute(
            """
            SELECT
                s.id,
                s.task_id,
                s.user_id,
                s.status,
                t.reward_amount,
                t.verification_type
            FROM mining_submissions AS s
            JOIN mining_tasks AS t ON t.id = s.task_id
            WHERE s.id = ?
            """,
            (submission_id,),
        ).fetchone()

        if not submission:
            raise SubmissionNotFound("Submission not found")

        if submission["verification_type"] != "manual":
            raise SubmissionValidationError(
                "Automatic submissions must be reviewed through provider verification"
            )

        if submission["status"] not in ("pending", "under_review"):
            raise SubmissionValidationError(
                "This submission has already received a final decision"
            )

        reviewed_at = db.execute(
            "SELECT CURRENT_TIMESTAMP"
        ).fetchone()[0]

        db.execute(
            """
            UPDATE mining_submissions
            SET status = ?,
                reviewer_id = ?,
                review_note = ?,
                reviewed_at = ?
            WHERE id = ?
            """,
            (
                decision,
                reviewer_user_id,
                review_note,
                reviewed_at if decision != "under_review" else None,
                submission_id,
            ),
        )

        if decision == "rejected":
            _restore_task_budget(
                db,
                submission["task_id"],
                submission["reward_amount"],
            )

        db.execute(
            """
            INSERT INTO mining_admin_actions (
                administrator_id,
                action,
                target_type,
                target_id,
                details
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                reviewer_user_id,
                "submission_" + decision,
                "submission",
                submission_id,
                json.dumps(
                    {
                        "user_id": submission["user_id"],
                        "task_id": submission["task_id"],
                        "review_note": review_note,
                    },
                    separators=(",", ":"),
                ),
            ),
        )

        db.commit()

        return {
            "submission_id": submission_id,
            "status": decision,
            "reviewer_id": reviewer_user_id,
            "wallet_credited": False,
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def process_provider_callback(
    provider_id,
    provider_secret,
    callback_id,
    submission_id,
    provider_reference,
    verification_status,
):
    provider_id = _positive_id(provider_id, "provider_id")
    submission_id = _positive_id(submission_id, "submission_id")

    callback_id = _text(callback_id, "callback_id", 200, required=True)
    provider_reference = _text(
        provider_reference,
        "provider_reference",
        200,
        required=True,
    )

    if not isinstance(provider_secret, str) or not provider_secret:
        raise ProviderAuthenticationError("Invalid provider credentials")

    if verification_status not in ("verified", "failed"):
        raise SubmissionValidationError("Invalid verification status")

    supplied_hash = hashlib.sha256(
        provider_secret.encode("utf-8")
    ).hexdigest()

    db = connect()

    try:
        db.execute("BEGIN IMMEDIATE")

        provider = db.execute(
            """
            SELECT id, callback_secret_hash, status
            FROM mining_providers
            WHERE id = ?
            """,
            (provider_id,),
        ).fetchone()

        if (
            not provider
            or provider["status"] != "active"
            or not provider["callback_secret_hash"]
            or not hmac.compare_digest(
                provider["callback_secret_hash"],
                supplied_hash,
            )
        ):
            raise ProviderAuthenticationError(
                "Provider authentication failed"
            )

        existing = db.execute(
            """
            SELECT
                v.id,
                v.submission_id,
                v.provider_reference,
                v.verification_status,
                t.provider_id
            FROM mining_verifications AS v
            JOIN mining_submissions AS s ON s.id = v.submission_id
            JOIN mining_tasks AS t ON t.id = s.task_id
            WHERE v.callback_id = ?
            """,
            (callback_id,),
        ).fetchone()

        if existing:
            same_request = (
                existing["submission_id"] == submission_id
                and existing["provider_id"] == provider_id
                and existing["provider_reference"] == provider_reference
                and existing["verification_status"] == verification_status
            )

            if not same_request:
                raise SubmissionValidationError(
                    "Callback ID was already used for a different result"
                )

            db.commit()

            return {
                "submission_id": submission_id,
                "verification_status": verification_status,
                "idempotent_replay": True,
                "wallet_credited": False,
            }

        submission = db.execute(
            """
            SELECT
                s.id,
                s.task_id,
                s.user_id,
                s.status,
                t.reward_amount,
                t.verification_type,
                t.provider_id
            FROM mining_submissions AS s
            JOIN mining_tasks AS t ON t.id = s.task_id
            WHERE s.id = ?
            """,
            (submission_id,),
        ).fetchone()

        if not submission:
            raise SubmissionNotFound("Submission not found")

        if (
            submission["verification_type"] != "automatic"
            or submission["provider_id"] != provider_id
        ):
            raise SubmissionPermissionError(
                "Provider is not authorized to verify this submission"
            )

        if submission["status"] != "pending":
            raise SubmissionValidationError(
                "Submission has already received a final decision"
            )

        verification = db.execute(
            """
            SELECT id, verification_status
            FROM mining_verifications
            WHERE submission_id = ?
            """,
            (submission_id,),
        ).fetchone()

        if not verification or verification["verification_status"] != "pending":
            raise SubmissionValidationError(
                "Verification is not awaiting a callback"
            )

        verified_at = db.execute(
            "SELECT CURRENT_TIMESTAMP"
        ).fetchone()[0]

        db.execute(
            """
            UPDATE mining_verifications
            SET provider_reference = ?,
                verification_status = ?,
                callback_id = ?,
                verified_at = ?
            WHERE submission_id = ?
            """,
            (
                provider_reference,
                verification_status,
                callback_id,
                verified_at,
                submission_id,
            ),
        )

        submission_status = (
            "approved" if verification_status == "verified" else "rejected"
        )

        db.execute(
            """
            UPDATE mining_submissions
            SET status = ?,
                review_note = ?,
                reviewed_at = ?
            WHERE id = ?
            """,
            (
                submission_status,
                "Provider verification: " + verification_status,
                verified_at,
                submission_id,
            ),
        )

        if verification_status == "failed":
            _restore_task_budget(
                db,
                submission["task_id"],
                submission["reward_amount"],
            )

            db.execute(
                """
                INSERT INTO mining_risk_events (
                    user_id,
                    event_type,
                    severity,
                    details
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    submission["user_id"],
                    "provider_verification_failed",
                    "low",
                    json.dumps(
                        {
                            "submission_id": submission_id,
                            "provider_id": provider_id,
                            "callback_id": callback_id,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )

        db.commit()

        return {
            "submission_id": submission_id,
            "verification_status": verification_status,
            "submission_status": submission_status,
            "idempotent_replay": False,
            "wallet_credited": False,
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def get_submission(submission_id):
    submission_id = _positive_id(submission_id, "submission_id")

    db = connect()

    try:
        row = db.execute(
            """
            SELECT
                s.id,
                s.task_id,
                s.user_id,
                s.evidence,
                s.status,
                s.reviewer_id,
                s.review_note,
                s.submitted_at,
                s.reviewed_at,
                t.title AS task_title,
                t.reward_amount,
                t.reward_currency,
                t.verification_type,
                v.provider_reference,
                v.verification_status,
                v.verified_at
            FROM mining_submissions AS s
            JOIN mining_tasks AS t ON t.id = s.task_id
            LEFT JOIN mining_verifications AS v
                ON v.submission_id = s.id
            WHERE s.id = ?
            """,
            (submission_id,),
        ).fetchone()

        return dict(row) if row else None

    finally:
        db.close()


def list_user_submissions(user_id, limit=30, offset=0):
    user_id = _positive_id(user_id, "user_id")

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise SubmissionValidationError("limit must be between 1 and 100")

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise SubmissionValidationError("offset must be non-negative")

    db = connect()

    try:
        rows = db.execute(
            """
            SELECT
                s.id,
                s.task_id,
                s.status,
                s.submitted_at,
                s.reviewed_at,
                t.title AS task_title,
                t.reward_amount,
                t.reward_currency,
                t.verification_type
            FROM mining_submissions AS s
            JOIN mining_tasks AS t ON t.id = s.task_id
            WHERE s.user_id = ?
            ORDER BY s.id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        db.close()
