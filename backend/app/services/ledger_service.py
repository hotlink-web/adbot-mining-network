import sqlite3

from backend.app.database.mining_db import connect, get_connection


class LedgerError(Exception):
    pass


class IdempotencyConflict(LedgerError):
    pass


class AccountValidationError(LedgerError):
    pass


class InsufficientFundsError(LedgerError):
    pass


def _normalize_entries(entries):
    if not isinstance(entries, (list, tuple)) or len(entries) < 2:
        raise LedgerError("At least two ledger entries are required")

    normalized = []
    seen_accounts = set()

    for entry in entries:
        if isinstance(entry, dict):
            account_id = entry.get("account_id")
            amount = entry.get("amount")
        else:
            try:
                account_id, amount = entry
            except (TypeError, ValueError):
                raise LedgerError("Invalid ledger entry format")

        if isinstance(account_id, bool) or not isinstance(account_id, int):
            raise LedgerError("Account ID must be an integer")

        if account_id <= 0:
            raise LedgerError("Account ID must be positive")

        if isinstance(amount, bool) or not isinstance(amount, int):
            raise LedgerError("Ledger amount must be an integer")

        if amount == 0:
            raise LedgerError("Zero-value entries are not allowed")

        if account_id in seen_accounts:
            raise LedgerError("Duplicate account in transaction")

        seen_accounts.add(account_id)
        normalized.append((account_id, amount))

    return sorted(normalized)


def _get_transaction_entries(connection, transaction_id):
    rows = connection.execute(
        """
        SELECT account_id, amount
        FROM mining_ledger_entries
        WHERE transaction_id = ?
        ORDER BY account_id
        """,
        (transaction_id,)
    ).fetchall()

    return sorted(
        (row["account_id"], row["amount"])
        for row in rows
    )


def post_transaction(
    reference,
    idempotency_key,
    transaction_type,
    entries,
    description=None
):
    """
    Atomically create and post a balanced ledger transaction.

    Each entry is either:
        (account_id, amount)

    Or:
        {"account_id": account_id, "amount": amount}

    Amounts are integer minor units.
    Positive and negative entries must balance separately
    for every currency.
    """

    if not isinstance(reference, str) or not reference.strip():
        raise LedgerError("Transaction reference is required")

    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise LedgerError("Idempotency key is required")

    if not isinstance(transaction_type, str) or not transaction_type.strip():
        raise LedgerError("Transaction type is required")

    normalized = _normalize_entries(entries)

    connection = connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """
            SELECT *
            FROM mining_ledger_transactions
            WHERE idempotency_key = ?
            """,
            (idempotency_key,)
        ).fetchone()

        if existing:
            existing_entries = _get_transaction_entries(
                connection,
                existing["id"]
            )

            same_request = (
                existing["reference"] == reference
                and existing["transaction_type"] == transaction_type
                and existing["description"] == description
                and existing_entries == normalized
            )

            if not same_request or existing["status"] != "posted":
                raise IdempotencyConflict(
                    "Idempotency key was already used for a different "
                    "or incomplete transaction"
                )

            result = {
                "id": existing["id"],
                "reference": existing["reference"],
                "status": existing["status"],
                "posted_at": existing["posted_at"],
                "idempotent_replay": True
            }

            connection.commit()
            return result

        reference_exists = connection.execute(
            """
            SELECT id
            FROM mining_ledger_transactions
            WHERE reference = ?
            """,
            (reference,)
        ).fetchone()

        if reference_exists:
            raise IdempotencyConflict(
                "Transaction reference already exists"
            )

        account_ids = [account_id for account_id, _ in normalized]
        placeholders = ",".join("?" for _ in account_ids)

        account_rows = connection.execute(
            f"""
            SELECT id, currency, user_id
            FROM mining_wallet_accounts
            WHERE id IN ({placeholders})
            """,
            account_ids
        ).fetchall()

        accounts = {
            row["id"]: {
                "currency": row["currency"],
                "user_id": row["user_id"]
            }
            for row in account_rows
        }

        if len(accounts) != len(account_ids):
            raise AccountValidationError(
                "One or more wallet accounts do not exist"
            )

        currency_totals = {}

        for account_id, amount in normalized:
            currency = accounts[account_id]["currency"]
            currency_totals[currency] = (
                currency_totals.get(currency, 0) + amount
            )

            if accounts[account_id]["user_id"] is not None:
                current_balance = connection.execute(
                    """
                    SELECT COALESCE(SUM(e.amount), 0)
                    FROM mining_ledger_entries e
                    JOIN mining_ledger_transactions t
                        ON t.id = e.transaction_id
                    WHERE e.account_id = ?
                      AND t.status = 'posted'
                    """,
                    (account_id,)
                ).fetchone()[0]

                if current_balance + amount < 0:
                    raise InsufficientFundsError(
                        "User wallet balance cannot become negative"
                    )

        if any(total != 0 for total in currency_totals.values()):
            raise LedgerError(
                "Transaction is not balanced for every currency"
            )

        cursor = connection.execute(
            """
            INSERT INTO mining_ledger_transactions (
                reference,
                idempotency_key,
                transaction_type,
                description
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                reference,
                idempotency_key,
                transaction_type,
                description
            )
        )

        transaction_id = cursor.lastrowid

        connection.executemany(
            """
            INSERT INTO mining_ledger_entries (
                transaction_id,
                account_id,
                amount
            )
            VALUES (?, ?, ?)
            """,
            [
                (transaction_id, account_id, amount)
                for account_id, amount in normalized
            ]
        )

        connection.execute(
            """
            UPDATE mining_ledger_transactions
            SET status = 'posted'
            WHERE id = ?
            """,
            (transaction_id,)
        )

        transaction = connection.execute(
            """
            SELECT id, reference, status, posted_at
            FROM mining_ledger_transactions
            WHERE id = ?
            """,
            (transaction_id,)
        ).fetchone()

        connection.commit()

        return {
            "id": transaction["id"],
            "reference": transaction["reference"],
            "status": transaction["status"],
            "posted_at": transaction["posted_at"],
            "idempotent_replay": False
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_transaction(reference):
    with get_connection() as connection:
        transaction = connection.execute(
            """
            SELECT *
            FROM mining_ledger_transactions
            WHERE reference = ?
            """,
            (reference,)
        ).fetchone()

        if transaction is None:
            return None

        entries = connection.execute(
            """
            SELECT
                e.account_id,
                a.account_type,
                a.currency,
                e.amount
            FROM mining_ledger_entries e
            JOIN mining_wallet_accounts a
                ON a.id = e.account_id
            WHERE e.transaction_id = ?
            ORDER BY e.id
            """,
            (transaction["id"],)
        ).fetchall()

        return {
            "id": transaction["id"],
            "reference": transaction["reference"],
            "idempotency_key": transaction["idempotency_key"],
            "transaction_type": transaction["transaction_type"],
            "status": transaction["status"],
            "description": transaction["description"],
            "created_at": transaction["created_at"],
            "posted_at": transaction["posted_at"],
            "entries": [dict(row) for row in entries]
        }
