# ⚡ PocketBolt — P2P payments prototype (TEST MODE)

PocketBolt is a Cash App / Venmo-style peer-to-peer payments app: sign up, link a bank
or a debit/credit card, add cash, send money to anyone by **$handle, phone or email**,
request money, accept/decline requests, cash out, and share a QR pay link.

> **Test mode only. No real money moves.** Every bank, card and balance is simulated
> unless you plug in Plaid **sandbox** and Stripe **test** keys (live Stripe keys are refused).

## Features
- Sign up / log in (passwords hashed with Werkzeug scrypt, HttpOnly session cookie, login rate limit)
- Wallet balance, stored as integer cents in SQLite
- Link bank accounts (built-in sandbox, or **Plaid Link** when keys are set)
- Link debit/credit cards (built-in sandbox with Luhn/expiry checks, or **Stripe Elements + SetupIntent** when keys are set). Only brand + last 4 are kept.
- Send money from your balance **or** straight from a linked card/bank
- Request money, accept (pay) / decline / cancel requests
- Add cash and cash out (simulated)
- Activity feed and pending-request badge, auto-refresh every 10 s plus on app resume; toast, vibration and (opt-in) system notification for incoming money
- Tabs: Home, Pay (big amount keypad + recipient autocomplete), Activity, Cards, Profile
- QR code and deep link for your pay handle (`/pay/<handle>?amount=12`), Web Share API, copy link
- Contact Picker API (Android Chrome) to pick a recipient from phone contacts
- Installable **PWA**: manifest, icons, service worker (offline app shell; money data is never cached), app shortcuts. On a phone, “Add to Home Screen” makes it open full-screen like a native app.
- Safety rules: no overdrafts (checked in code **and** by a DB `CHECK` constraint), every transfer is one atomic `BEGIN IMMEDIATE` transaction, $5,000 per-transaction and $50,000 wallet caps, can’t pay yourself, can’t use someone else’s card, a request can only be paid once.

## Run it
```bash
cd pocketbolt
./run.sh                 # creates venv, installs deps, seeds demo users, starts on :8000
# or manually:
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python seed.py
./venv/bin/gunicorn -w 1 --threads 8 -b 0.0.0.0:8000 app:app     # or: ./venv/bin/python app.py
```
Open http://localhost:8000. Demo logins: **denzel**, **maya**, **jordan** — password **demo1234**.

Sandbox test values: bank login `user_good` / `pass_good`; card `4242 4242 4242 4242` (works),
`4000 0000 0000 0002` (declined), any future expiry, any CVC.

## Tests
```bash
./venv/bin/python -m pytest -q
```
33 tests cover amount parsing, password hashing, auth, sends by handle/email/phone, overdraft
blocking, rollback on failure, the DB constraint, 50-thread concurrent transfers (exactly 20 of
50 $5 sends succeed from a $100 wallet, money is conserved), request pay/decline/cancel,
add cash / cash out, card validation, card numbers never stored, and PWA assets.

## Real provider test modes (optional)
Set these env vars and restart. The app switches automatically and the Cards tab shows which is active.

| Feature | Env vars | What happens |
|---|---|---|
| Bank linking | `PLAID_CLIENT_ID`, `PLAID_SECRET`, (`PLAID_ENV=sandbox`) | Opens real Plaid Link; exchanges `public_token` → `access_token`, reads the account mask. `PlaidProvider.stripe_bank_token()` is the Plaid→Stripe processor handoff for real ACH. |
| Cards | `STRIPE_SECRET_KEY` (sk_test_…), `STRIPE_PUBLISHABLE_KEY` | Stripe Elements card form + SetupIntent saves the card on a Stripe Customer; Add cash / pay-from-card create an off-session PaymentIntent. |

Other env: `PORT`, `SECRET_KEY`, `POCKETBOLT_DB`, `COOKIE_SECURE=1` (set when served over HTTPS).

## How the big apps do it (research notes)
- **Cash App / Venmo** keep a stored-value wallet balance; payments between users are just ledger
  entries inside the company. Money enters/leaves through linked debit cards (card networks; instant
  cash-out via push-to-card for a fee) and bank accounts (ACH, 1–3 days). They hold money
  transmitter licenses in nearly every US state, and partner banks hold customer funds.
- **Zelle** is different: bank-to-bank in real time over the banks’ own network, no wallet balance.
- **Developers** typically use **Plaid** (Link → `public_token` → `/item/public_token/exchange` →
  `access_token`; Auth for account/routing numbers; processor tokens to hand the account to Stripe or
  Dwolla) and **Stripe** (SetupIntent to save cards, PaymentIntent to charge, **Stripe Connect** for
  paying out to users, **Stripe Treasury / Financial Accounts** for bank-held balances).
- **Blockchain angle (later add-on, not core):** send USDC stablecoin between user wallets (e.g. on
  Base or Solana) via Circle’s APIs or Coinbase’s developer platform, or Bitcoin Lightning. It adds
  wallet custody, compliance (FinCEN MSB rules, Travel Rule) and price/on-ramp work, so it fits best as
  an optional “send crypto” feature after the dollar wallet works.

## Going live — what real money requires
1. **Legal:** moving other people’s money is money transmission. Either get state **money transmitter
   licenses** (+ FinCEN MSB registration; expensive, slow) or — far more realistic — partner with a
   **bank / Banking-as-a-Service** provider that holds funds for you (e.g. **Stripe Treasury / Connect**,
   Unit, Treasury Prime, Synctera, Column). The partner’s compliance team must approve the product.
2. **KYC / AML:** verify identity (name, DOB, address, SSN last 4) before sending (Stripe Identity,
   Persona, Alloy), sanctions/OFAC screening, transaction monitoring, limits, fraud rules, dispute
   handling. Terms of Service, Privacy Policy, E-SIGN consent, Reg E error-resolution process.
3. **Provider approval:** Plaid **Production** access (application + security questionnaire) and a
   Stripe account activated for live mode with Connect enabled.
4. **Security:** HTTPS everywhere, encrypt Plaid access tokens at rest (KMS), CSRF tokens, 2FA/passkeys,
   audit logs, backups, Postgres instead of SQLite, idempotency keys and refunds for provider charges
   (today a card charge then failed credit would need a manual refund).
5. **Hosting:** deploy to an HTTPS host such as **Render**, **Railway** or **Fly.io** (start command
   `gunicorn -w 2 --threads 8 -b 0.0.0.0:$PORT app:app`, set `COOKIE_SECURE=1`, add a persistent disk or Postgres).
6. **App stores (optional):** the PWA already installs from the browser. For Apple App Store / Google
   Play, wrap it with **Capacitor** (iOS needs a Mac + Apple Developer account, $99/yr; Google Play $25 one-time).

## Project layout
```
app.py          Flask app + JSON API + PWA routes
ledger.py       atomic money movement (transfer, deposit, withdraw, requests)
providers.py    Simulated / Plaid / Stripe providers
db.py           SQLite schema
seed.py         demo users
static/         index.html, app.js, styles.css, manifest, sw.js, icons
tests/          pytest suite
run.sh          one-command start
```
