import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATABASE_PATH = Path(
    os.environ.get(
        "MINING_DATABASE_PATH",
        str(PROJECT_ROOT / "mining.db")
    )
)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mining_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    first_name TEXT,
    country TEXT,
    ton_wallet_address TEXT,
    referred_by INTEGER REFERENCES mining_users(id),
    referral_code TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'suspended', 'banned')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mining_sponsors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'inactive', 'suspended')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mining_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    callback_secret_hash TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'inactive', 'suspended')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mining_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    provider_id INTEGER REFERENCES mining_providers(id),
    sponsor_id INTEGER REFERENCES mining_sponsors(id),
    reward_amount INTEGER NOT NULL CHECK(reward_amount > 0),
    reward_currency TEXT NOT NULL DEFAULT 'USD',
    task_budget INTEGER NOT NULL CHECK(task_budget >= 0),
    remaining_budget INTEGER NOT NULL CHECK(remaining_budget >= 0),
    verification_type TEXT NOT NULL
        CHECK(verification_type IN ('manual', 'automatic')),
    instructions TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft', 'active', 'paused', 'completed', 'expired')),
    starts_at TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(remaining_budget <= task_budget)
);

CREATE TABLE IF NOT EXISTS mining_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES mining_tasks(id),
    user_id INTEGER NOT NULL REFERENCES mining_users(id),
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'rejected', 'under_review')),
    reviewer_id INTEGER REFERENCES mining_users(id),
    review_note TEXT,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    UNIQUE(task_id, user_id)
);

CREATE TABLE IF NOT EXISTS mining_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES mining_submissions(id),
    provider_reference TEXT,
    verification_status TEXT NOT NULL
        CHECK(verification_status IN ('pending', 'verified', 'failed')),
    callback_id TEXT UNIQUE,
    verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mining_settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sponsor_id INTEGER REFERENCES mining_sponsors(id),
    provider_id INTEGER REFERENCES mining_providers(id),
    external_reference TEXT NOT NULL UNIQUE,
    gross_amount INTEGER NOT NULL CHECK(gross_amount >= 0),
    fees INTEGER NOT NULL DEFAULT 0 CHECK(fees >= 0),
    net_amount INTEGER NOT NULL CHECK(net_amount >= 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'settled', 'reversed')),
    settled_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(net_amount = gross_amount - fees)
);

CREATE TABLE IF NOT EXISTS mining_settlement_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id INTEGER NOT NULL
        REFERENCES mining_settlements(id),
    submission_id INTEGER NOT NULL UNIQUE
        REFERENCES mining_submissions(id),
    allocated_amount INTEGER NOT NULL CHECK(allocated_amount > 0),
    provider_fee INTEGER NOT NULL DEFAULT 0 CHECK(provider_fee >= 0),
    worker_reward INTEGER NOT NULL CHECK(worker_reward > 0),
    referral_commission INTEGER NOT NULL DEFAULT 0
        CHECK(referral_commission >= 0),
    platform_revenue INTEGER NOT NULL DEFAULT 0
        CHECK(platform_revenue >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(settlement_id, submission_id),
    CHECK(
        allocated_amount =
        provider_fee + worker_reward + referral_commission + platform_revenue
    )
);

CREATE TABLE IF NOT EXISTS mining_wallet_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES mining_users(id),
    account_type TEXT NOT NULL,
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(
        (account_type IN ('pending', 'available', 'reserved', 'paid')
         AND user_id IS NOT NULL)
        OR
        (account_type LIKE 'system_%' AND user_id IS NULL)
    ),
    UNIQUE(user_id, account_type, currency)
);

CREATE TABLE IF NOT EXISTS mining_ledger_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    transaction_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK(status IN ('draft', 'posted', 'reversed')),
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    posted_at TEXT
);

CREATE TABLE IF NOT EXISTS mining_ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL
        REFERENCES mining_ledger_transactions(id),
    account_id INTEGER NOT NULL
        REFERENCES mining_wallet_accounts(id),
    amount INTEGER NOT NULL CHECK(amount != 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mining_referrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_user_id INTEGER NOT NULL REFERENCES mining_users(id),
    referred_user_id INTEGER NOT NULL UNIQUE REFERENCES mining_users(id),
    referral_code TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(referrer_user_id != referred_user_id)
);

CREATE TABLE IF NOT EXISTS mining_referral_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    referral_id INTEGER NOT NULL REFERENCES mining_referrals(id),
    settlement_id INTEGER NOT NULL REFERENCES mining_settlements(id),
    commission_rate INTEGER NOT NULL CHECK(commission_rate = 10),
    commission_amount INTEGER NOT NULL CHECK(commission_amount >= 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'credited', 'reversed')),
    ledger_transaction_id INTEGER
        REFERENCES mining_ledger_transactions(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(referral_id, settlement_id)
);

CREATE TABLE IF NOT EXISTS mining_withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES mining_users(id),
    amount INTEGER NOT NULL CHECK(amount > 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    ton_amount INTEGER,
    exchange_rate INTEGER,
    ton_wallet_address TEXT NOT NULL,
    network TEXT NOT NULL DEFAULT 'testnet',
    status TEXT NOT NULL DEFAULT 'requested'
        CHECK(status IN (
            'requested', 'reserved', 'approved',
            'processing', 'paid', 'rejected', 'failed'
        )),
    payout_reference TEXT UNIQUE,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS mining_admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    administrator_id INTEGER NOT NULL REFERENCES mining_users(id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mining_risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES mining_users(id),
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL
        CHECK(severity IN ('low', 'medium', 'high', 'critical')),
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mining_system_accounts_unique
    ON mining_wallet_accounts(account_type, currency)
    WHERE user_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_mining_tasks_status
    ON mining_tasks(status);

CREATE INDEX IF NOT EXISTS idx_mining_submissions_user
    ON mining_submissions(user_id, status);

CREATE INDEX IF NOT EXISTS idx_mining_settlements_status
    ON mining_settlements(status);

CREATE INDEX IF NOT EXISTS idx_mining_ledger_account
    ON mining_ledger_entries(account_id);

CREATE INDEX IF NOT EXISTS idx_mining_ledger_transaction
    ON mining_ledger_entries(transaction_id);

CREATE INDEX IF NOT EXISTS idx_mining_withdrawals_user
    ON mining_withdrawals(user_id, status);

CREATE INDEX IF NOT EXISTS idx_mining_referrals_referrer
    ON mining_referrals(referrer_user_id);


CREATE TABLE IF NOT EXISTS mining_ledger_reversals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_transaction_id INTEGER NOT NULL UNIQUE
        REFERENCES mining_ledger_transactions(id),
    reversal_transaction_id INTEGER NOT NULL UNIQUE
        REFERENCES mining_ledger_transactions(id),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK(original_transaction_id != reversal_transaction_id)
);

CREATE TRIGGER IF NOT EXISTS mining_reversal_insert_guard
BEFORE INSERT ON mining_ledger_reversals
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM mining_ledger_transactions
            WHERE id = NEW.original_transaction_id
              AND status = 'posted'
        )
        THEN RAISE(ABORT, 'Original transaction must be posted')
    END;

    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1 FROM mining_ledger_transactions
            WHERE id = NEW.reversal_transaction_id
              AND status = 'posted'
        )
        THEN RAISE(ABORT, 'Reversal transaction must be posted')
    END;

    SELECT CASE
        WHEN (
            SELECT COUNT(*)
            FROM mining_ledger_entries
            WHERE transaction_id = NEW.original_transaction_id
        ) != (
            SELECT COUNT(*)
            FROM mining_ledger_entries
            WHERE transaction_id = NEW.reversal_transaction_id
        )
        OR EXISTS (
            SELECT 1
            FROM mining_ledger_entries original_entry
            WHERE original_entry.transaction_id = NEW.original_transaction_id
              AND NOT EXISTS (
                  SELECT 1
                  FROM mining_ledger_entries reversal_entry
                  WHERE reversal_entry.transaction_id = NEW.reversal_transaction_id
                    AND reversal_entry.account_id = original_entry.account_id
                    AND reversal_entry.amount = -original_entry.amount
              )
        )
        THEN RAISE(ABORT, 'Reversal entries must exactly oppose original entries')
    END;
END;

CREATE TRIGGER IF NOT EXISTS mining_reversal_no_update
BEFORE UPDATE ON mining_ledger_reversals
BEGIN
    SELECT RAISE(ABORT, 'Reversal records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS mining_reversal_no_delete
BEFORE DELETE ON mining_ledger_reversals
BEGIN
    SELECT RAISE(ABORT, 'Reversal records cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS mining_transaction_initial_status_guard
BEFORE INSERT ON mining_ledger_transactions
WHEN NEW.status != 'draft'
BEGIN
    SELECT RAISE(ABORT, 'New ledger transactions must start as draft');
END;

CREATE TRIGGER IF NOT EXISTS mining_transaction_status_transition_guard
BEFORE UPDATE OF status ON mining_ledger_transactions
WHEN NEW.status IS NOT OLD.status
 AND NOT (OLD.status = 'draft' AND NEW.status = 'posted')
BEGIN
    SELECT RAISE(ABORT, 'Only draft transactions can be posted; reversals require a new transaction');
END;

CREATE TRIGGER IF NOT EXISTS mining_ledger_entry_insert_guard
BEFORE INSERT ON mining_ledger_entries
BEGIN
    SELECT CASE
        WHEN (
            SELECT status FROM mining_ledger_transactions
            WHERE id = NEW.transaction_id
        ) != 'draft'
        THEN RAISE(ABORT, 'Ledger transaction is not editable')
    END;
END;

CREATE TRIGGER IF NOT EXISTS mining_ledger_entry_no_update
BEFORE UPDATE ON mining_ledger_entries
BEGIN
    SELECT RAISE(ABORT, 'Ledger entries are immutable');
END;

CREATE TRIGGER IF NOT EXISTS mining_ledger_entry_no_delete
BEFORE DELETE ON mining_ledger_entries
BEGIN
    SELECT RAISE(ABORT, 'Ledger entries cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS mining_transaction_post_guard
BEFORE UPDATE OF status ON mining_ledger_transactions
WHEN NEW.status = 'posted' AND OLD.status = 'draft'
BEGIN
    SELECT CASE
        WHEN (
            SELECT COUNT(*)
            FROM mining_ledger_entries
            WHERE transaction_id = NEW.id
        ) < 2
        THEN RAISE(ABORT, 'At least two ledger entries are required')
    END;

    SELECT CASE
        WHEN EXISTS (
            SELECT a.currency
            FROM mining_ledger_entries e
            JOIN mining_wallet_accounts a ON a.id = e.account_id
            WHERE e.transaction_id = NEW.id
            GROUP BY a.currency
            HAVING SUM(e.amount) != 0
        )
        THEN RAISE(ABORT, 'Ledger transaction is not balanced')
    END;
END;

CREATE TRIGGER IF NOT EXISTS mining_transaction_post_timestamp
AFTER UPDATE OF status ON mining_ledger_transactions
WHEN NEW.status = 'posted' AND OLD.status = 'draft'
BEGIN
    UPDATE mining_ledger_transactions
    SET posted_at = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS mining_transaction_no_unpost
BEFORE UPDATE OF status ON mining_ledger_transactions
WHEN OLD.status = 'posted' AND NEW.status != 'posted'
BEGIN
    SELECT RAISE(ABORT, 'Posted transactions cannot be changed');
END;

CREATE TRIGGER IF NOT EXISTS mining_posted_transaction_immutable
BEFORE UPDATE ON mining_ledger_transactions
WHEN OLD.status = 'posted'
 AND (
    NEW.reference IS NOT OLD.reference
    OR NEW.idempotency_key IS NOT OLD.idempotency_key
    OR NEW.transaction_type IS NOT OLD.transaction_type
    OR NEW.status IS NOT OLD.status
    OR NEW.description IS NOT OLD.description
    OR NEW.created_at IS NOT OLD.created_at
 )
BEGIN
    SELECT RAISE(ABORT, 'Posted transactions are immutable; create a reversal');
END;

CREATE TRIGGER IF NOT EXISTS mining_transaction_no_delete
BEFORE DELETE ON mining_ledger_transactions
WHEN OLD.status = 'posted'
BEGIN
    SELECT RAISE(ABORT, 'Posted transactions cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS mining_user_balance_nonnegative
BEFORE UPDATE OF status ON mining_ledger_transactions
WHEN NEW.status = 'posted' AND OLD.status = 'draft'
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM mining_ledger_entries proposed
            JOIN mining_wallet_accounts account
                ON account.id = proposed.account_id
            WHERE proposed.transaction_id = NEW.id
              AND account.user_id IS NOT NULL
              AND (
                  COALESCE((
                      SELECT SUM(existing.amount)
                      FROM mining_ledger_entries existing
                      JOIN mining_ledger_transactions posted
                          ON posted.id = existing.transaction_id
                      WHERE existing.account_id = account.id
                        AND posted.status = 'posted'
                  ), 0)
                  +
                  (
                      SELECT SUM(current.amount)
                      FROM mining_ledger_entries current
                      WHERE current.transaction_id = NEW.id
                        AND current.account_id = account.id
                  )
              ) < 0
        )
        THEN RAISE(ABORT, 'User wallet balance cannot become negative')
    END;
END;
"""


def connect():
    connection = sqlite3.connect(
        str(DATABASE_PATH),
        timeout=30,
        isolation_level="DEFERRED"
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def get_connection():
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = connect()
    try:
        connection.executescript(SCHEMA)

        system_accounts = [
            ("system_sponsor_receivable", "USD"),
            ("system_sponsor_revenue", "USD"),
            ("system_payment_fee_expense", "USD"),
            ("system_provider_fee_expense", "USD"),
            ("system_platform_revenue", "USD"),
            ("system_reward_expense", "USD"),
            ("system_referral_expense", "USD"),
            ("system_payout_clearing", "USD"),
        ]

        for account_type, currency in system_accounts:
            connection.execute(
                """
                INSERT OR IGNORE INTO mining_wallet_accounts
                    (user_id, account_type, currency)
                VALUES (NULL, ?, ?)
                """,
                (account_type, currency)
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return DATABASE_PATH


def get_table_names():
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

    return [row["name"] for row in rows]


if __name__ == "__main__":
    path = init_database()
    print("Mining database initialized:", path)
    print("Tables:")
    for table in get_table_names():
        print("-", table)
