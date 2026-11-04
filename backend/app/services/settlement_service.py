import sqlite3

from backend.app.database.mining_db import connect


class SettlementError(Exception):
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
            raise SettlementError("Invalid ledger entry")

        aggregated[account_id] = aggregated.get(account_id, 0) + amount

    normalized = sorted(
        (account_id, amount)
        for account_id, amount in aggregated.items()
        if amount != 0
    )

    if len(normalized) < 2:
        raise SettlementError("Ledger transaction requires at least two entries")

    totals = {}

    for account_id, amount in normalized:
        row = db.execute(
            "SELECT currency, user_id FROM mining_wallet_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()

        if row is None:
            raise SettlementError("Ledger account not found")

        currency = row["currency"]
        totals[currency] = totals.get(currency, 0) + amount

        if row["user_id"] is not None:
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
                raise SettlementError("User wallet balance cannot become negative")

    if any(value != 0 for value in totals.values()):
        raise SettlementError("Ledger entries are not balanced")

    existing = db.execute(
        """
        SELECT id FROM mining_ledger_transactions
        WHERE reference = ? OR idempotency_key = ?
        """,
        (reference, reference),
    ).fetchone()

    if existing:
        raise SettlementError("Ledger reference already exists")

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

def record_sponsor_payment(
    sponsor_id,
    external_reference,
    gross_amount,
    fees,
    currency,
    administrator_id,
    evidence=None,
):
    if not external_reference or not external_reference.strip():
        raise SettlementError("External payment reference is required")

    if isinstance(gross_amount, bool) or not isinstance(gross_amount, int):
        raise SettlementError("Gross amount must be an integer")

    if isinstance(fees, bool) or not isinstance(fees, int):
        raise SettlementError("Fees must be an integer")

    if gross_amount <= 0 or fees < 0 or fees > gross_amount:
        raise SettlementError("Invalid payment amount or fees")

    currency = currency.upper()
    if currency not in ("USD", "TON"):
        raise SettlementError("Unsupported settlement currency")

    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")

        sponsor = db.execute(
            "SELECT id FROM mining_sponsors WHERE id = ? AND status = 'active'",
            (sponsor_id,),
        ).fetchone()

        if sponsor is None:
            raise SettlementError("Active sponsor not found")

        cursor = db.execute(
            """
            INSERT INTO mining_settlements
                (sponsor_id, external_reference, gross_amount,
                 fees, net_amount, currency, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                sponsor_id,
                external_reference.strip(),
                gross_amount,
                fees,
                gross_amount - fees,
                currency,
            ),
        )

        settlement_id = cursor.lastrowid

        db.execute(
            """
            INSERT INTO mining_admin_actions
                (administrator_id, action, target_type, target_id, details)
            VALUES (?, 'sponsor_payment_recorded', 'settlement', ?, ?)
            """,
            (
                administrator_id,
                settlement_id,
                f"Reference={external_reference}; currency={currency}; "
                f"gross={gross_amount}; fees={fees}; evidence={evidence or ''}",
            ),
        )

        db.commit()
        return {"settlement_id": settlement_id, "status": "pending"}

    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise SettlementError(
            "Payment reference already exists or violates database constraints"
        ) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def confirm_sponsor_payment(
    settlement_id,
    administrator_id,
    allocations,
):
    if not isinstance(allocations, list) or not allocations:
        raise SettlementError("At least one allocation is required")

    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")

        settlement = db.execute(
            """
            SELECT * FROM mining_settlements
            WHERE id = ? AND status = 'pending'
            """,
            (settlement_id,),
        ).fetchone()

        if settlement is None:
            raise SettlementError("Pending settlement not found")

        currency = settlement["currency"]
        sponsor_id = settlement["sponsor_id"]
        net_amount = settlement["net_amount"]

        clearing_id = _account(
            db, "system_sponsor_cash_clearing", currency
        )
        revenue_id = _account(
            db, "system_sponsor_revenue", currency
        )
        fee_id = _account(
            db, "system_payment_fee_expense", currency
        )

        receipt_entries = [(clearing_id, settlement["gross_amount"])]

        if settlement["fees"]:
            receipt_entries.append((fee_id, -settlement["fees"]))

        receipt_entries.append((revenue_id, -net_amount))

        _post_ledger(
            db,
            f"settlement-receipt-{settlement_id}",
            "sponsor_receipt",
            receipt_entries,
            f"Confirmed sponsor payment {settlement['external_reference']}",
        )

        total_allocated = 0
        payout_entries = [(revenue_id, 0)]
        payout_entries.clear()

        for allocation in allocations:
            submission_id = allocation.get("submission_id")
            allocated_amount = allocation.get("amount")
            provider_fee = allocation.get("provider_fee", 0)

            if (
                isinstance(submission_id, bool)
                or not isinstance(submission_id, int)
                or submission_id <= 0
                or isinstance(allocated_amount, bool)
                or not isinstance(allocated_amount, int)
                or allocated_amount <= 0
                or isinstance(provider_fee, bool)
                or not isinstance(provider_fee, int)
                or provider_fee < 0
                or provider_fee >= allocated_amount
            ):
                raise SettlementError("Invalid submission allocation")

            submission = db.execute(
                """
                SELECT s.id, s.user_id, s.status, t.sponsor_id,
                       t.reward_amount, t.reward_currency
                FROM mining_submissions s
                JOIN mining_tasks t ON t.id = s.task_id
                WHERE s.id = ?
                """,
                (submission_id,),
            ).fetchone()

            if submission is None or submission["status"] != "approved":
                raise SettlementError("Only approved submissions can be allocated")

            if (
                submission["sponsor_id"] != sponsor_id
                or submission["reward_currency"] != currency
            ):
                raise SettlementError("Submission sponsor or currency mismatch")

            net_after_provider_fee = allocated_amount - provider_fee
            if net_after_provider_fee < submission["reward_amount"]:
                raise SettlementError(
                    "Net allocation after provider fee is below worker reward"
                )

            if db.execute(
                """
                SELECT 1 FROM mining_settlement_allocations
                WHERE submission_id = ?
                """,
                (submission_id,),
            ).fetchone():
                raise SettlementError("Submission has already been allocated")

            worker_reward = submission["reward_amount"]

            referral = db.execute(
                """
                SELECT id, referrer_user_id
                FROM mining_referrals
                WHERE referred_user_id = ?
                """,
                (submission["user_id"],),
            ).fetchone()

            commission = (net_after_provider_fee * 10) // 100 if referral else 0

            if worker_reward + commission > net_after_provider_fee:
                raise SettlementError(
                    "Allocation is insufficient for worker reward and referral"
                )

            platform_remainder = net_after_provider_fee - worker_reward - commission

            worker_account = _account(
                db, "available", currency, submission["user_id"]
            )

            payout_entries.append((worker_account, worker_reward))
            if provider_fee:
                payout_entries.append((fee_id, provider_fee))

            if commission:
                referrer_account = _account(
                    db, "available", currency, referral["referrer_user_id"]
                )
                payout_entries.append((referrer_account, commission))

                db.execute(
                    """
                    INSERT INTO mining_referral_rewards
                        (referral_id, settlement_id, commission_rate,
                         commission_amount, status)
                    VALUES (?, ?, 10, ?, 'credited')
                    """,
                    (referral["id"], settlement_id, commission),
                )

            if platform_remainder:
                platform_account = _account(
                    db, "system_platform_revenue", currency
                )
                payout_entries.append((platform_account, platform_remainder))

            db.execute(
                """
                INSERT INTO mining_settlement_allocations
                    (settlement_id, submission_id, allocated_amount,
                     provider_fee, worker_reward, referral_commission,
                     platform_revenue)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    settlement_id,
                    submission_id,
                    allocated_amount,
                    provider_fee,
                    worker_reward,
                    commission,
                    platform_remainder,
                ),
            )

            total_allocated += allocated_amount

        if total_allocated > net_amount:
            raise SettlementError("Allocations exceed settled net revenue")

        payout_entries.append((revenue_id, -total_allocated))

        _post_ledger(
            db,
            f"settlement-allocation-{settlement_id}",
            "settlement_allocation",
            payout_entries,
            f"Rewards and revenue for settlement {settlement_id}",
        )

        db.execute(
            """
            UPDATE mining_settlements
            SET status = 'settled', settled_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (settlement_id,),
        )

        db.execute(
            """
            INSERT INTO mining_admin_actions
                (administrator_id, action, target_type, target_id, details)
            VALUES (?, 'sponsor_payment_confirmed', 'settlement', ?, ?)
            """,
            (
                administrator_id,
                settlement_id,
                f"Allocated {total_allocated} of {net_amount} {currency}",
            ),
        )

        db.commit()

        return {
            "settlement_id": settlement_id,
            "status": "settled",
            "allocated_amount": total_allocated,
            "unallocated_amount": net_amount - total_allocated,
            "currency": currency,
        }

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
