# AdBot Mining Network

A separate Telegram Mini App and task-based rewards platform.

## Main features

- Telegram Mini App integration
- Worldwide user registration
- Sponsored task marketplace
- External task provider integrations
- Automatic and manual task verification
- Referral rewards (10% of eligible settled task revenue)
- Independent mining wallet and transaction ledger
- TON withdrawal requests
- Owner administration and fraud monitoring

## Architecture

- `bot/` — Telegram bot integration
- `frontend/` — Telegram Mini App
- `backend/` — Mining API and business logic
- `migrations/` — Database migrations
- `tests/` — Automated tests
- `docs/` — Technical documentation

## Financial separation

Mining uses its own database, wallet, transactions, rewards,
referrals and withdrawals.

It must never modify or share balances with the existing
AdBot advertising wallet or campaign payment system.

## Security

- Validate Telegram Mini App authentication on the backend.
- Never trust balances or user IDs supplied by the browser.
- Never request users' TON recovery phrases or private keys.
- Record financial changes in an auditable transaction ledger.
- Only credit rewards after task verification and eligible revenue settlement.
- Keep pending earnings separate from withdrawable balances.

## Development status

Initial repository setup.
