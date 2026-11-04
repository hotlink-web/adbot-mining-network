import re
import sqlite3

from backend.app.database.mining_db import connect
from backend.app.core.config import settings


class WithdrawalError(Exception):
    pass


def _account(db, account_type, currency, user_id=None):
    row = db.execute(
        """
        SELECT id FROM mining_wallet_accounts
        WHERE account_type = ? AND currency = ? AND user_id IS ?
        """,
        (account_type, currency, user_id),
    ).fetchone()

    if row:
        return row["id"]

    db.execute(
        """
        INSERT INTO mining_wallet_accounts
            (user_id, account_type, currency)
        VALUES (?, ?, ?)
        """,
        (user_id, account_type, currency),
    )
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _post_ledger(db, reference, transaction_type, entries, description):
    aggregated = {}

    for account_id, amount in entries:
        if (
            isinstance(account_id, bool)
            or not isinstance(account_id, int)
            or account_id <= 0
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount == 0
        ):
            raise WithdrawalError("Invalid ledger entry")

        aggregated[account_id] = aggregated.get(account_id, 0) + amount

    normalized = sorted(
        (account_id, amount)
        for account_id, amount in aggregated.items()
        if amount
    )

    if len(normalized) < 2:
        raise WithdrawalError("Ledger transaction requires at least two entries")

    totals = {}

    for account_id, amount in normalized:
        account = db.execute(
            "SELECT currency, user_id FROM mining_wallet_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()

        if account is None:
            raise WithdrawalError("Ledger account not found")

        currency = account["currency"]
        totals[currency] = totals.get(currency, 0) + amount

        if account["user_id"] is not None:
            balance = db.execute(
                """
                SELECT COALESCE(SUM(e.amount), 0)
                FROM mining_ledger_entries e
                JOIN mining_ledger_transactions t
                    ON t.id = e.transaction_id
                WHERE e.account_id = ? AND t.status = 'posted'
                """,
                (account_id,),
            ).fetchone()[0]

            if balance + amount < 0:
                raise WithdrawalError("Insufficient wallet balance")

    if any(total != 0 for total in totals.values()):
        raise WithdrawalError("Ledger entries are not balanced")

    existing = db.execute(
        """
        SELECT id FROM mining_ledger_transactions
        WHERE reference = ? OR idempotency_key = ?
        """,
        (reference, reference),
    ).fetchone()

    if existing:
        raise WithdrawalError("Ledger reference already exists")

    cursor = db.execute(
        """
        INSERT INTO mining_ledger_transactions
            (reference, idempotency_key, transaction_type, description)
        VALUES (?, ?, ?, ?)
        """,
        (reference, reference, transaction_type, description),
    )

    transaction_id = cursor.lastrowid

    db.executemany(
        """
        INSERT INTO mining_ledger_entries
            (transaction_id, account_id, amount)
        VALUES (?, ?, ?)
        """,
        [
            (transaction_id, account_id, amount)
            for account_id, amount in normalized
        ],
    )

    db.execute(
        """
        UPDATE mining_ledger_transactions
        SET status = 'posted'
        WHERE id = ?
        """,
        (transaction_id,),
    )

    return transaction_id


def _validate_address(address):
    if not isinstance(address, str):
        raise WithdrawalError("Invalid TON wallet address")

    address = address.strip()

    friendly = re.fullmatch(r"[A-Za-z0-9_-]{48}", address)
    raw = re.fullmatch(r"(-1|0):[0-9a-fA-F]{64}", address)

    if not (friendly or raw):
        raise WithdrawalError("Enter a valid TON friendly or raw wallet address")

    return address


def request_withdrawal(user_id, amount, wallet_address):
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise WithdrawalError("Invalid user")

    if isinstance(amount, bool) or not isinstance(amount, int):
        raise WithdrawalError("Withdrawal amount must be an integer number of cents")

    if amount < settings.MINIMUM_WITHDRAWAL_USD_CENTS:
        raise WithdrawalError("Minimum withdrawal is $2.00")

    address = _validate_address(wallet_address)
    network = settings.TON_NETWORK

    if network not in ("testnet", "mainnet"):
        raise WithdrawalError("TON network configuration is invalid")

    db = connect()

    try:
        db.execute("BEGIN IMMEDIATE")

        user = db.execute(
            "SELECT id, status FROM mining_users WHERE id = ?",
            (user_id,),
        ).fetchone()

        if user is None or user["status"] != "active":
            raise WithdrawalError("Active mining account not found")

        available_id = _account(db, "available", "USD", user_id)
        reserved_id = _account(db, "reserved", "USD", user_id)

        balance = db.execute(
            """
            SELECT COALESCE(SUM(e.amount), 0)
            FROM mining_ledger_entries e
            JOIN mining_ledger_transactions t
                ON t.id = e.transaction_id
            WHERE e.account_id = ? AND t.status = 'posted'
            """,
            (available_id,),
        ).fetchone()[0]

        if balance < amount:
            raise WithdrawalError("Insufficient available USD balance")

        cursor = db.execute(
            """
            INSERT INTO mining_withdrawals
                (user_id, amount, currency, ton_wallet_address, network, status)
            VALUES (?, ?, 'USD', ?, ?, 'reserved')
            """,
            (user_id, amount, address, network),
        )

        withdrawal_id = cursor.lastrowid

        _post_ledger(
            db,
            f"withdrawal-reserve-{withdrawal_id}",
            "withdrawal_reserve",
            [
                (available_id, -amount),
                (reserved_id, amount),
            ],
            f"Reserve funds for TON withdrawal #{withdrawal_id}",
        )

        db.commit()

        return {
            "withdrawal_id": withdrawal_id,
            "amount_usd_cents": amount,
            "status": "reserved",
            "network": network,
            "message": "Funds reserved. Awaiting administrator processing.",
        }

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_user_withdrawals(user_id, limit=30, offset=0):
    db = connect()

    try:
        rows = db.execute(
            """
            SELECT id, amount, currency, ton_amount, exchange_rate,
                   ton_wallet_address, network, status, payout_reference,
                   requested_at, processed_at
            FROM mining_withdrawals
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        db.close()


def list_admin_withdrawals(status=None, limit=100, offset=0):
    db = connect()

    try:
        query = """
            SELECT w.*, u.telegram_id
            FROM mining_withdrawals w
            JOIN mining_users u ON u.id = w.user_id
        """
        params = []

        if status:
            query += " WHERE w.status = ?"
            params.append(status)

        query += " ORDER BY w.id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        return [dict(row) for row in db.execute(query, params).fetchall()]
    finally:
        db.close()


def mark_withdrawal_paid(
    withdrawal_id,
    administrator_id,
    ton_amount,
    exchange_rate,
    payout_reference,
):
    if isinstance(ton_amount, bool) or not isinstance(ton_amount, int) or ton_amount <= 0:
        raise WithdrawalError("TON amount must be positive nanoton")

    if isinstance(exchange_rate, bool) or not isinstance(exchange_rate, int) or exchange_rate <= 0:
        raise WithdrawalError("Exchange rate must be positive USD cents per TON")

    if not payout_reference or not payout_reference.strip():
        raise WithdrawalError("Blockchain payout reference is required")

    payout_reference = payout_reference.strip()

    db = connect()

    try:
        db.execute("BEGIN IMMEDIATE")

        withdrawal = db.execute(
            "SELECT * FROM mining_withdrawals WHERE id = ?",
            (withdrawal_id,),
        ).fetchone()

        if withdrawal is None:
            raise WithdrawalError("Withdrawal not found")

        if withdrawal["status"] != "reserved":
            raise WithdrawalError("Only reserved withdrawals can be paid")

        duplicate = db.execute(
            """
            SELECT id FROM mining_withdrawals
            WHERE payout_reference = ?
            """,
            (payout_reference,),
        ).fetchone()

        if duplicate:
            raise WithdrawalError("Payout reference has already been used")

        user_id = withdrawal["user_id"]
        amount = withdrawal["amount"]

        reserved_id = _account(db, "reserved", "USD", user_id)
        paid_id = _account(db, "paid", "USD", user_id)

        _post_ledger(
            db,
            f"withdrawal-paid-{withdrawal_id}",
            "withdrawal_paid",
            [
                (reserved_id, -amount),
                (paid_id, amount),
            ],
            f"Record completed TON withdrawal #{withdrawal_id}",
        )

        db.execute(
            """
            UPDATE mining_withdrawals
            SET status = 'paid',
                ton_amount = ?,
                exchange_rate = ?,
                payout_reference = ?,
                processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (ton_amount, exchange_rate, payout_reference, withdrawal_id),
        )

        db.execute(
            """
            INSERT INTO mining_admin_actions
                (administrator_id, action, target_type, target_id, details)
            VALUES (?, 'withdrawal_paid', 'withdrawal', ?, ?)
            """,
            (
                administrator_id,
                withdrawal_id,
                f"TON nanoton={ton_amount}; rate_usd_cents_per_ton={exchange_rate}; payout_reference={payout_reference}",
            ),
        )

        db.commit()

        return {
            "withdrawal_id": withdrawal_id,
            "status": "paid",
            "ton_amount_nanoton": ton_amount,
            "exchange_rate_usd_cents_per_ton": exchange_rate,
            "payout_reference": payout_reference,
        }

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def release_withdrawal(withdrawal_id, administrator_id, reason, failed=False):
    if not reason or not reason.strip():
        raise WithdrawalError("A rejection or failure reason is required")

    db = connect()

    try:
        db.execute("BEGIN IMMEDIATE")

        withdrawal = db.execute(
            "SELECT * FROM mining_withdrawals WHERE id = ?",
            (withdrawal_id,),
        ).fetchone()

        if withdrawal is None:
            raise WithdrawalError("Withdrawal not found")

        if withdrawal["status"] != "reserved":
            raise WithdrawalError("Only reserved withdrawals can be released")

        user_id = withdrawal["user_id"]
        amount = withdrawal["amount"]

        reserved_id = _account(db, "reserved", "USD", user_id)
        available_id = _account(db, "available", "USD", user_id)

        final_status = "failed" if failed else "rejected"

        _post_ledger(
            db,
            f"withdrawal-release-{withdrawal_id}",
            "withdrawal_release",
            [
                (reserved_id, -amount),
                (available_id, amount),
            ],
            f"Release funds for withdrawal #{withdrawal_id}: {reason.strip()}",
        )

        db.execute(
            """
            UPDATE mining_withdrawals
            SET status = ?, processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (final_status, withdrawal_id),
        )

        db.execute(
            """
            INSERT INTO mining_admin_actions
                (administrator_id, action, target_type, target_id, details)
            VALUES (?, ?, 'withdrawal', ?, ?)
            """,
            (
                administrator_id,
                f"withdrawal_{final_status}",
                withdrawal_id,
                reason.strip(),
            ),
        )

        db.commit()

        return {
            "withdrawal_id": withdrawal_id,
            "status": final_status,
            "released_usd_cents": amount,
        }

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
