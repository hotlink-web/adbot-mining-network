# AdBot Mining Network — Database Design

## 1. Database independence

Development:
- SQLite database: mining.db

Production:
- PostgreSQL database with persistent storage.

The mining database must never share tables, balances,
transactions or payment records with the existing AdBot.

## 2. User accounts

### mining_users
- id
- telegram_id (unique)
- username
- first_name
- country
- ton_wallet_address
- referred_by
- status
- created_at
- updated_at

Telegram identity must be authenticated by validating
Telegram Mini App initData on the backend.

## 3. Tasks

### mining_tasks
- id
- title
- description
- category
- provider_id
- sponsor_id
- reward_amount
- reward_currency
- task_budget
- remaining_budget
- verification_type
- instructions
- status
- starts_at
- expires_at
- created_at

### mining_submissions
- id
- task_id
- user_id
- evidence
- status
- reviewer_id
- review_note
- submitted_at
- reviewed_at

### mining_verifications
- id
- submission_id
- provider_reference
- verification_status
- callback_id
- verified_at
- created_at

Provider callbacks must be authenticated and idempotent.

## 4. Sponsors and providers

### mining_sponsors
- id
- name
- contact
- status
- created_at

### mining_providers
- id
- name
- provider_type
- callback_secret_hash
- status
- created_at

### mining_settlements
- id
- sponsor_id
- provider_id
- external_reference
- gross_amount
- fees
- net_amount
- currency
- status
- settled_at
- created_at

Pending provider revenue must never be treated as settled money.

## 5. Wallet and financial ledger

### mining_wallet_accounts
- id
- user_id
- account_type
- currency
- created_at

Account types:
- pending
- available
- reserved
- paid

### mining_ledger_transactions
- id
- reference
- transaction_type
- status
- description
- created_at

### mining_ledger_entries
- id
- transaction_id
- account_id
- amount
- created_at

Rules:
- Use double-entry accounting.
- Each transaction must balance to zero per currency.
- Entries are immutable.
- Corrections use reversing transactions.
- Never directly edit a user's balance.
- Displayed balances are calculated from ledger entries.
- Every financial operation must be idempotent.

## 6. Referrals

### mining_referrals
- id
- referrer_user_id
- referred_user_id
- referral_code
- created_at

### mining_referral_rewards
- id
- referral_id
- settlement_id
- commission_rate
- commission_amount
- status
- ledger_transaction_id
- created_at

Commission:
10% of eligible settled task revenue after applicable
provider fees, refunds and reversals.

No commission for registration alone.

## 7. Withdrawals

### mining_withdrawals
- id
- user_id
- amount
- currency
- ton_amount
- exchange_rate
- ton_wallet_address
- network
- status
- payout_reference
- requested_at
- processed_at

Rules:
- Minimum withdrawal: $2 equivalent in TON.
- Reserve available funds when a valid request is accepted.
- Only authorized administrators can approve payouts.
- Record payout confirmation before marking a withdrawal paid.
- Failed or rejected withdrawals must release reserved funds.
- Never request private keys or recovery phrases.

## 8. Administration and security

### mining_admin_actions
- id
- administrator_id
- action
- target_type
- target_id
- details
- created_at

### mining_risk_events
- id
- user_id
- event_type
- severity
- details
- created_at

## 9. Required database protections

- Unique Telegram user IDs.
- Unique provider callback IDs.
- Unique external settlement references.
- Unique task submission per applicable task/user rule.
- Foreign keys enabled.
- Financial operations performed inside database transactions.
- Ledger entries cannot be updated or deleted by application code.
- Withdrawal and settlement operations must be auditable.

## 10. Development sequence

1. Database initialization and schema.
2. Telegram authentication and user registration.
3. Task marketplace.
4. Task submissions and verification.
5. Sponsor settlement and rewards.
6. Referral commission.
7. Wallet ledger and balances.
8. TON withdrawal requests.
9. Owner administration.
10. External provider integrations.
11. Security and financial tests.

No real withdrawals or earning claims until sponsor
settlement and payout operations have been verified.
