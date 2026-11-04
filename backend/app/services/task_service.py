from datetime import datetime, timezone

from backend.app.database.mining_db import connect


class TaskServiceError(Exception):
    pass


ALLOWED_STATUSES = {
    "draft",
    "active",
    "paused",
    "completed",
    "expired",
}

ALLOWED_VERIFICATION_TYPES = {
    "manual",
    "automatic",
}

ALLOWED_TRANSITIONS = {
    "draft": {"active"},
    "active": {"paused", "completed", "expired"},
    "paused": {"active", "completed", "expired"},
    "completed": set(),
    "expired": set(),
}


def _positive_integer(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TaskServiceError(f"{field} must be a positive integer")


def _optional_id(value, field):
    if value is not None:
        _positive_integer(value, field)


def _validate_text(value, field, maximum=5000):
    if not isinstance(value, str) or not value.strip():
        raise TaskServiceError(f"{field} is required")

    value = value.strip()

    if len(value) > maximum:
        raise TaskServiceError(f"{field} is too long")

    return value


def _validate_optional_date(value, field):
    if value is None:
        return None

    if not isinstance(value, str) or not value.strip():
        raise TaskServiceError(f"{field} must be a valid ISO date")

    value = value.strip()

    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TaskServiceError(f"{field} must be a valid ISO date") from exc

    return value


def _get_task(connection, task_id):
    return connection.execute(
        """
        SELECT
            tasks.*,
            sponsors.name AS sponsor_name,
            providers.name AS provider_name
        FROM mining_tasks AS tasks
        LEFT JOIN mining_sponsors AS sponsors
            ON sponsors.id = tasks.sponsor_id
        LEFT JOIN mining_providers AS providers
            ON providers.id = tasks.provider_id
        WHERE tasks.id = ?
        """,
        (task_id,),
    ).fetchone()


def list_active_tasks(category=None, limit=30, offset=0):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise TaskServiceError("Limit must be between 1 and 100")

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise TaskServiceError("Offset cannot be negative")

    if category is not None:
        category = _validate_text(category, "Category", 80)

    connection = connect()

    try:
        query = """
            SELECT
                tasks.*,
                sponsors.name AS sponsor_name
            FROM mining_tasks AS tasks
            LEFT JOIN mining_sponsors AS sponsors
                ON sponsors.id = tasks.sponsor_id
            LEFT JOIN mining_providers AS providers
                ON providers.id = tasks.provider_id
            WHERE tasks.status = 'active'
              AND tasks.remaining_budget >= tasks.reward_amount
              AND (tasks.starts_at IS NULL OR tasks.starts_at <= CURRENT_TIMESTAMP)
              AND (tasks.expires_at IS NULL OR tasks.expires_at > CURRENT_TIMESTAMP)
              AND (
                  tasks.sponsor_id IS NULL
                  OR sponsors.status = 'active'
              )
              AND (
                  tasks.provider_id IS NULL
                  OR providers.status = 'active'
              )
        """

        parameters = []

        if category:
            query += " AND tasks.category = ?"
            parameters.append(category)

        query += """
            ORDER BY tasks.created_at DESC, tasks.id DESC
            LIMIT ? OFFSET ?
        """

        parameters.extend((limit, offset))

        rows = connection.execute(query, parameters).fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()


def get_task_details(task_id):
    _positive_integer(task_id, "Task ID")

    connection = connect()

    try:
        task = _get_task(connection, task_id)

        if task is None or task["status"] != "active":
            return None

        availability = connection.execute(
            """
            SELECT 1
            FROM mining_tasks AS t
            LEFT JOIN mining_sponsors AS s
                ON s.id = t.sponsor_id
            LEFT JOIN mining_providers AS p
                ON p.id = t.provider_id
            WHERE t.id = ?
              AND (t.starts_at IS NULL OR datetime(t.starts_at) <= CURRENT_TIMESTAMP)
              AND (t.expires_at IS NULL OR datetime(t.expires_at) > CURRENT_TIMESTAMP)
              AND (t.sponsor_id IS NULL OR s.status = 'active')
              AND (t.provider_id IS NULL OR p.status = 'active')
            """,
            (task_id,),
        ).fetchone()

        if availability is None:
            return None

        if task["remaining_budget"] < task["reward_amount"]:
            return None

        return dict(task)

    finally:
        connection.close()


def create_task(
    title,
    description,
    category,
    reward_amount,
    reward_currency="USD",
    task_budget=0,
    verification_type="manual",
    instructions=None,
    sponsor_id=None,
    provider_id=None,
    starts_at=None,
    expires_at=None,
):
    """
    Create a task as a draft.

    Amounts use integer minor units:
    USD cents and TON nanoton.
    """

    title = _validate_text(title, "Title", 160)
    description = _validate_text(description, "Description", 10000)
    category = _validate_text(category, "Category", 80)

    _positive_integer(reward_amount, "Reward amount")

    if isinstance(task_budget, bool) or not isinstance(task_budget, int) or task_budget < 0:
        raise TaskServiceError("Task budget cannot be negative")

    if task_budget < reward_amount:
        raise TaskServiceError("Task budget must cover at least one reward")

    if not isinstance(reward_currency, str):
        raise TaskServiceError("Invalid reward currency")

    reward_currency = reward_currency.strip().upper()

    if reward_currency not in ("USD", "TON"):
        raise TaskServiceError("Unsupported reward currency")

    if verification_type not in ALLOWED_VERIFICATION_TYPES:
        raise TaskServiceError("Invalid verification type")

    if instructions is not None:
        instructions = _validate_text(instructions, "Instructions", 10000)

    _optional_id(sponsor_id, "Sponsor ID")
    _optional_id(provider_id, "Provider ID")

    starts_at = _validate_optional_date(starts_at, "Start date")
    expires_at = _validate_optional_date(expires_at, "Expiry date")

    if starts_at and expires_at and starts_at >= expires_at:
        raise TaskServiceError("Expiry must be after the start date")

    connection = connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        if sponsor_id is not None:
            sponsor = connection.execute(
                "SELECT status FROM mining_sponsors WHERE id = ?",
                (sponsor_id,),
            ).fetchone()

            if sponsor is None or sponsor["status"] != "active":
                raise TaskServiceError("Sponsor does not exist or is inactive")

        if provider_id is not None:
            provider = connection.execute(
                "SELECT status FROM mining_providers WHERE id = ?",
                (provider_id,),
            ).fetchone()

            if provider is None or provider["status"] != "active":
                raise TaskServiceError("Provider does not exist or is inactive")

        cursor = connection.execute(
            """
            INSERT INTO mining_tasks (
                title,
                description,
                category,
                provider_id,
                sponsor_id,
                reward_amount,
                reward_currency,
                task_budget,
                remaining_budget,
                verification_type,
                instructions,
                status,
                starts_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                title,
                description,
                category,
                provider_id,
                sponsor_id,
                reward_amount,
                reward_currency,
                task_budget,
                task_budget,
                verification_type,
                instructions,
                starts_at,
                expires_at,
            ),
        )

        task = _get_task(connection, cursor.lastrowid)

        connection.commit()

        return dict(task)

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def change_task_status(task_id, new_status):
    _positive_integer(task_id, "Task ID")

    if new_status not in ALLOWED_STATUSES:
        raise TaskServiceError("Invalid task status")

    connection = connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        task = connection.execute(
            "SELECT * FROM mining_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()

        if task is None:
            raise TaskServiceError("Task not found")

        current_status = task["status"]

        if new_status == current_status:
            connection.commit()
            return dict(_get_task(connection, task_id))

        if new_status not in ALLOWED_TRANSITIONS[current_status]:
            raise TaskServiceError(
                f"Cannot change task from {current_status} to {new_status}"
            )

        if new_status == "active":
            if task["remaining_budget"] < task["reward_amount"]:
                raise TaskServiceError("Insufficient remaining task budget")

            if task["expires_at"] and task["expires_at"] <= datetime.now(timezone.utc).isoformat():
                raise TaskServiceError("Task expiry date has passed")

            if task["sponsor_id"] is not None:
                sponsor = connection.execute(
                    "SELECT status FROM mining_sponsors WHERE id = ?",
                    (task["sponsor_id"],),
                ).fetchone()

                if sponsor is None or sponsor["status"] != "active":
                    raise TaskServiceError("Task sponsor is not active")

            if task["provider_id"] is not None:
                provider = connection.execute(
                    "SELECT status FROM mining_providers WHERE id = ?",
                    (task["provider_id"],),
                ).fetchone()

                if provider is None or provider["status"] != "active":
                    raise TaskServiceError("Task provider is not active")

        connection.execute(
            """
            UPDATE mining_tasks
            SET status = ?
            WHERE id = ?
            """,
            (new_status, task_id),
        )

        updated = _get_task(connection, task_id)

        connection.commit()

        return dict(updated)

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def list_admin_tasks(status=None, limit=50, offset=0):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise TaskServiceError("Limit must be between 1 and 100")

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise TaskServiceError("Offset cannot be negative")

    if status is not None and status not in ALLOWED_STATUSES:
        raise TaskServiceError("Invalid task status")

    connection = connect()

    try:
        query = """
            SELECT
                tasks.*,
                sponsors.name AS sponsor_name,
                providers.name AS provider_name
            FROM mining_tasks AS tasks
            LEFT JOIN mining_sponsors AS sponsors
                ON sponsors.id = tasks.sponsor_id
            LEFT JOIN mining_providers AS providers
                ON providers.id = tasks.provider_id
        """

        parameters = []

        if status:
            query += " WHERE tasks.status = ?"
            parameters.append(status)

        query += " ORDER BY tasks.id DESC LIMIT ? OFFSET ?"

        parameters.extend((limit, offset))

        rows = connection.execute(query, parameters).fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()
