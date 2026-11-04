from backend.app.database.mining_db import connect


class WalletServiceError(Exception):
    pass


def _validate_user_id(user_id):
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
    ):
        raise WalletServiceError("Invalid mining user ID")


def get_wallet_summary(user_id):
    """
    Calculate wallet balances from posted ledger entries.

    Amounts are returned as integer minor units:
    USD = cents.
    TON = nanoton.
    """

    _validate_user_id(user_id)

    connection = connect()

    try:
        user = connection.execute(
            """
            SELECT id
            FROM mining_users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if user is None:
            raise WalletServiceError("Mining user not found")

        rows = connection.execute(
            """
            SELECT
                accounts.account_type,
                accounts.currency,
                COALESCE(
                    SUM(
                        CASE
                            WHEN transactions.id IS NOT NULL
                            THEN entries.amount
                            ELSE 0
                        END
                    ),
                    0
                ) AS balance
            FROM mining_wallet_accounts AS accounts
            LEFT JOIN mining_ledger_entries AS entries
                ON entries.account_id = accounts.id
            LEFT JOIN mining_ledger_transactions AS transactions
                ON transactions.id = entries.transaction_id
                AND transactions.status = 'posted'
            WHERE accounts.user_id = ?
            GROUP BY
                accounts.account_type,
                accounts.currency
            ORDER BY
                accounts.currency,
                accounts.account_type
            """,
            (user_id,),
        ).fetchall()

        balances = {}

        for row in rows:
            account_type = row["account_type"]
            currency = row["currency"]

            balances.setdefault(currency, {})
            balances[currency][account_type] = int(row["balance"] or 0)

        return {
            "user_id": user_id,
            "balances": balances,
        }

    finally:
        connection.close()


def get_wallet_transactions(user_id, limit=20, offset=0):
    """
    Return posted ledger transactions involving the user's accounts.

    Amounts remain integer minor units.
    """

    _validate_user_id(user_id)

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 100
    ):
        raise WalletServiceError("Limit must be between 1 and 100")

    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
    ):
        raise WalletServiceError("Offset cannot be negative")

    connection = connect()

    try:
        user = connection.execute(
            """
            SELECT id
            FROM mining_users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if user is None:
            raise WalletServiceError("Mining user not found")

        rows = connection.execute(
            """
            SELECT
                transactions.id AS transaction_id,
                transactions.reference,
                transactions.transaction_type,
                transactions.description,
                transactions.created_at,
                transactions.posted_at,
                accounts.account_type,
                accounts.currency,
                entries.amount
            FROM mining_ledger_transactions AS transactions
            INNER JOIN mining_ledger_entries AS entries
                ON entries.transaction_id = transactions.id
            INNER JOIN mining_wallet_accounts AS accounts
                ON accounts.id = entries.account_id
            WHERE accounts.user_id = ?
              AND transactions.status = 'posted'
            ORDER BY
                transactions.id DESC,
                accounts.currency,
                accounts.account_type
            LIMIT ?
            OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:
        connection.close()
