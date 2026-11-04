from backend.app.database.mining_db import connect


class ReversalError(Exception):
    pass


class OriginalTransactionNotFound(ReversalError):
    pass


class AlreadyReversedError(ReversalError):
    pass


class ReversalConflictError(ReversalError):
    pass


class ReversalInsufficientFundsError(ReversalError):
    pass


def reverse_transaction(
    original_reference,
    reversal_reference,
    idempotency_key,
    reason=None,
):
    """
    Create an immutable, balanced reversal of a posted transaction.

    The original transaction is never edited.
    Reversal entries are exact opposites of the original entries.
    """

    for name, value in (
        ("Original reference", original_reference),
        ("Reversal reference", reversal_reference),
        ("Idempotency key", idempotency_key),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ReversalError(f"{name} is required")

    if reason is not None and not isinstance(reason, str):
        raise ReversalError("Reason must be text")

    original_reference = original_reference.strip()
    reversal_reference = reversal_reference.strip()
    idempotency_key = idempotency_key.strip()

    if original_reference == reversal_reference:
        raise ReversalError("Reversal must have a different reference")

    connection = connect()

    try:
        connection.execute("BEGIN IMMEDIATE")

        original = connection.execute(
            """
            SELECT id, reference, status
            FROM mining_ledger_transactions
            WHERE reference = ?
            """,
            (original_reference,),
        ).fetchone()

        if original is None:
            raise OriginalTransactionNotFound(
                "Original transaction does not exist"
            )

        original_id = original["id"]

        existing_reversal = connection.execute(
            """
            SELECT
                r.id,
                r.reversal_transaction_id,
                r.reason,
                t.reference,
                t.status,
                t.idempotency_key,
                t.posted_at
            FROM mining_ledger_reversals r
            JOIN mining_ledger_transactions t
                ON t.id = r.reversal_transaction_id
            WHERE r.original_transaction_id = ?
            """,
            (original_id,),
        ).fetchone()

        if existing_reversal:
            same_request = (
                existing_reversal["reference"] == reversal_reference
                and existing_reversal["idempotency_key"] == idempotency_key
                and existing_reversal["reason"] == reason
            )

            if not same_request:
                raise AlreadyReversedError(
                    "This transaction has already been reversed"
                )

            result = {
                "original_reference": original_reference,
                "reversal_reference": existing_reversal["reference"],
                "reversal_transaction_id": existing_reversal[
                    "reversal_transaction_id"
                ],
                "status": existing_reversal["status"],
                "posted_at": existing_reversal["posted_at"],
                "idempotent_replay": True,
            }

            connection.commit()
            return result

        if original["status"] != "posted":
            raise ReversalError(
                "Only posted transactions can be reversed"
            )

        if connection.execute(
            """
            SELECT 1
            FROM mining_ledger_transactions
            WHERE reference = ? OR idempotency_key = ?
            """,
            (reversal_reference, idempotency_key),
        ).fetchone():
            raise ReversalConflictError(
                "Reversal reference or idempotency key already exists"
            )

        original_entries = connection.execute(
            """
            SELECT account_id, amount
            FROM mining_ledger_entries
            WHERE transaction_id = ?
            ORDER BY account_id
            """,
            (original_id,),
        ).fetchall()

        if len(original_entries) < 2:
            raise ReversalError(
                "Original transaction does not have enough ledger entries"
            )

        reversal_entries = [
            (row["account_id"], -row["amount"])
            for row in original_entries
        ]

        account_ids = [account_id for account_id, _ in reversal_entries]
        placeholders = ",".join("?" for _ in account_ids)

        accounts = connection.execute(
            f"""
            SELECT id, currency, user_id
            FROM mining_wallet_accounts
            WHERE id IN ({placeholders})
            """,
            account_ids,
        ).fetchall()

        account_map = {
            row["id"]: {
                "currency": row["currency"],
                "user_id": row["user_id"],
            }
            for row in accounts
        }

        if len(account_map) != len(account_ids):
            raise ReversalError(
                "An original wallet account no longer exists"
            )

        currency_totals = {}

        for account_id, amount in reversal_entries:
            currency = account_map[account_id]["currency"]
            currency_totals[currency] = (
                currency_totals.get(currency, 0) + amount
            )

            if account_map[account_id]["user_id"] is not None:
                balance = connection.execute(
                    """
                    SELECT COALESCE(SUM(e.amount), 0)
                    FROM mining_ledger_entries e
                    JOIN mining_ledger_transactions t
                        ON t.id = e.transaction_id
                    WHERE e.account_id = ?
                      AND t.status = 'posted'
                    """,
                    (account_id,),
                ).fetchone()[0]

                if balance + amount < 0:
                    raise ReversalInsufficientFundsError(
                        "Reversal would make a user wallet balance negative"
                    )

        if any(total != 0 for total in currency_totals.values()):
            raise ReversalError(
                "Reversal does not balance separately by currency"
            )

        description = (
            f"Reversal of {original_reference}"
            + (f": {reason.strip()}" if reason and reason.strip() else "")
        )

        cursor = connection.execute(
            """
            INSERT INTO mining_ledger_transactions (
                reference,
                idempotency_key,
                transaction_type,
                description
            )
            VALUES (?, ?, 'reversal', ?)
            """,
            (reversal_reference, idempotency_key, description),
        )

        reversal_id = cursor.lastrowid

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
                (reversal_id, account_id, amount)
                for account_id, amount in reversal_entries
            ],
        )

        connection.execute(
            """
            UPDATE mining_ledger_transactions
            SET status = 'posted'
            WHERE id = ?
            """,
            (reversal_id,),
        )

        connection.execute(
            """
            INSERT INTO mining_ledger_reversals (
                original_transaction_id,
                reversal_transaction_id,
                reason
            )
            VALUES (?, ?, ?)
            """,
            (original_id, reversal_id, reason),
        )

        transaction = connection.execute(
            """
            SELECT id, reference, status, posted_at
            FROM mining_ledger_transactions
            WHERE id = ?
            """,
            (reversal_id,),
        ).fetchone()

        connection.commit()

        return {
            "original_reference": original_reference,
            "reversal_reference": transaction["reference"],
            "reversal_transaction_id": transaction["id"],
            "status": transaction["status"],
            "posted_at": transaction["posted_at"],
            "idempotent_replay": False,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_reversal(original_reference):
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                original.reference AS original_reference,
                reversal.reference AS reversal_reference,
                reversal.status AS reversal_status,
                reversal.posted_at,
                r.reason,
                r.created_at
            FROM mining_ledger_reversals r
            JOIN mining_ledger_transactions original
                ON original.id = r.original_transaction_id
            JOIN mining_ledger_transactions reversal
                ON reversal.id = r.reversal_transaction_id
            WHERE original.reference = ?
            """,
            (original_reference,),
        ).fetchone()

        return dict(row) if row else None
