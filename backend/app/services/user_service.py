import secrets
import sqlite3
import string

from backend.app.database.mining_db import connect


class UserServiceError(Exception):
    pass


class InvalidTelegramUser(UserServiceError):
    pass


class UserNotFound(UserServiceError):
    pass


WALLET_ACCOUNT_TYPES = (
    "pending",
    "available",
    "reserved",
    "paid",
)

DEFAULT_CURRENCY = "USD"


def _generate_referral_code(connection):
    alphabet = string.ascii_uppercase + string.digits

    for _ in range(20):
        code = "".join(
            secrets.choice(alphabet)
            for _ in range(10)
        )

        existing = connection.execute(
            """
            SELECT 1
            FROM mining_users
            WHERE referral_code = ?
            """,
            (code,),
        ).fetchone()

        if existing is None:
            return code

    raise UserServiceError("Unable to generate a unique referral code")


def _validate_telegram_user(telegram_user):
    if not isinstance(telegram_user, dict):
        raise InvalidTelegramUser("Invalid Telegram user data")

    telegram_id = telegram_user.get("id")

    if (
        isinstance(telegram_id, bool)
        or not isinstance(telegram_id, int)
        or telegram_id <= 0
    ):
        raise InvalidTelegramUser("Invalid Telegram user ID")

    username = telegram_user.get("username")
    first_name = telegram_user.get("first_name")

    if username is not None and not isinstance(username, str):
        raise InvalidTelegramUser("Invalid Telegram username")

    if first_name is not None and not isinstance(first_name, str):
        raise InvalidTelegramUser("Invalid Telegram first name")

    return {
        "telegram_id": telegram_id,
        "username": username.strip()[:64] if username else None,
        "first_name": first_name.strip()[:128] if first_name else None,
    }


def _provision_wallet_accounts(connection, user_id):
    for account_type in WALLET_ACCOUNT_TYPES:
        connection.execute(
            """
            INSERT OR IGNORE INTO mining_wallet_accounts (
                user_id,
                account_type,
                currency
            )
            VALUES (?, ?, ?)
            """,
            (user_id, account_type, DEFAULT_CURRENCY),
        )


def register_or_get_user(telegram_user, referral_code=None):
    """
    Register a user using verified Telegram identity data.

    Returns the existing or newly registered user.
    Wallet accounts are provisioned atomically with registration.

    Referral codes are accepted only during initial registration.
    Existing users cannot attach or change a referrer.
    """

    identity = _validate_telegram_user(telegram_user)

    if referral_code is not None:
        if not isinstance(referral_code, str):
            raise UserServiceError("Invalid referral code")

        referral_code = referral_code.strip().upper()

        if len(referral_code) > 32:
            raise UserServiceError("Invalid referral code")

        if not referral_code:
            referral_code = None

    connection = connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        user = connection.execute(
            """
            SELECT *
            FROM mining_users
            WHERE telegram_id = ?
            """,
            (identity["telegram_id"],),
        ).fetchone()

        is_new_user = user is None

        if is_new_user:
            referrer = None

            if referral_code:
                referrer = connection.execute(
                    """
                    SELECT id
                    FROM mining_users
                    WHERE referral_code = ?
                      AND status = 'active'
                    """,
                    (referral_code,),
                ).fetchone()

            referred_by = (
                referrer["id"]
                if referrer is not None
                else None
            )

            new_referral_code = _generate_referral_code(connection)

            cursor = connection.execute(
                """
                INSERT INTO mining_users (
                    telegram_id,
                    username,
                    first_name,
                    referred_by,
                    referral_code
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    identity["telegram_id"],
                    identity["username"],
                    identity["first_name"],
                    referred_by,
                    new_referral_code,
                ),
            )

            user_id = cursor.lastrowid

            if referred_by is not None:
                connection.execute(
                    """
                    INSERT INTO mining_referrals (
                        referrer_user_id,
                        referred_user_id,
                        referral_code
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        referred_by,
                        user_id,
                        referral_code,
                    ),
                )

        else:
            user_id = user["id"]

            if user["status"] != "active":
                raise UserServiceError(
                    "This mining account is not currently active"
                )

            connection.execute(
                """
                UPDATE mining_users
                SET
                    username = COALESCE(?, username),
                    first_name = COALESCE(?, first_name),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    identity["username"],
                    identity["first_name"],
                    user_id,
                ),
            )

        _provision_wallet_accounts(connection, user_id)

        result = connection.execute(
            """
            SELECT *
            FROM mining_users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        connection.commit()

        return {
            "user": dict(result),
            "is_new_user": is_new_user,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_user_by_telegram_id(telegram_id):
    if (
        isinstance(telegram_id, bool)
        or not isinstance(telegram_id, int)
        or telegram_id <= 0
    ):
        raise InvalidTelegramUser("Invalid Telegram user ID")

    connection = connect()

    try:
        user = connection.execute(
            """
            SELECT *
            FROM mining_users
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        ).fetchone()

        return dict(user) if user else None

    finally:
        connection.close()


def get_user_by_id(user_id):
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
    ):
        raise UserNotFound("Invalid mining user ID")

    connection = connect()

    try:
        user = connection.execute(
            """
            SELECT *
            FROM mining_users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        return dict(user) if user else None

    finally:
        connection.close()


def get_referral_summary(user_id):
    user = get_user_by_id(user_id)

    if user is None:
        raise UserNotFound("Mining user not found")

    connection = connect()

    try:
        referrals = connection.execute(
            """
            SELECT COUNT(*) AS total_referrals
            FROM mining_referrals
            WHERE referrer_user_id = ?
            """,
            (user_id,),
        ).fetchone()

        return {
            "referral_code": user["referral_code"],
            "total_referrals": referrals["total_referrals"],
        }

    finally:
        connection.close()
