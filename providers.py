"""Funding-source providers.

* SimulatedProvider  - always available, no keys, no real money. Mirrors the
  test conventions of Plaid sandbox (user_good / pass_good) and Stripe test
  cards (4242... succeeds, 4000 0000 0000 0002 declines).
* PlaidProvider      - real Plaid Link (sandbox by default) for bank linking.
  Active when PLAID_CLIENT_ID and PLAID_SECRET are set.
* StripeProvider     - real Stripe (test mode) for saving cards with a
  SetupIntent and charging them with a PaymentIntent. Active when
  STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY are set.

The real providers talk to the public REST APIs with `requests`, so no SDKs
are required. Keys are read only from environment variables.
"""
import os
import secrets
from datetime import date

import requests


class ProviderError(Exception):
    pass


# ---------------------------------------------------------------- simulated

SIM_BANKS = [
    {"id": "ins_sim_chase", "name": "Chase (sandbox)"},
    {"id": "ins_sim_boa", "name": "Bank of America (sandbox)"},
    {"id": "ins_sim_wells", "name": "Wells Fargo (sandbox)"},
    {"id": "ins_sim_cu", "name": "Navy Federal CU (sandbox)"},
    {"id": "ins_sim_chime", "name": "Chime (sandbox)"},
]

DECLINE_CARDS = {"4000000000000002", "4000000000009995"}


def luhn_ok(number):
    digits = [int(c) for c in number][::-1]
    total = 0
    for i, d in enumerate(digits):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def card_brand(number):
    if number.startswith("4"):
        return "visa"
    if number[:2] in {"51", "52", "53", "54", "55"} or 2221 <= int(number[:4]) <= 2720:
        return "mastercard"
    if number[:2] in {"34", "37"}:
        return "amex"
    if number.startswith("6011") or number.startswith("65"):
        return "discover"
    return "card"


class SimulatedProvider:
    name = "simulated"

    def banks(self):
        return SIM_BANKS

    def link_bank(self, institution_id, username, password, account_type="checking"):
        bank = next((b for b in SIM_BANKS if b["id"] == institution_id), None)
        if bank is None:
            raise ProviderError("Pick a bank from the list.")
        if (username, password) != ("user_good", "pass_good"):
            raise ProviderError("Sandbox login failed. Use username user_good and password pass_good.")
        last4 = f"{secrets.randbelow(10000):04d}"
        return {
            "label": f"{bank['name'].replace(' (sandbox)', '')} {account_type.title()}",
            "brand": bank["name"],
            "last4": last4,
            "external_id": "sim_bank_" + secrets.token_hex(8),
        }

    def link_card(self, number, exp_month, exp_year, cvc, funding="debit"):
        number = "".join(c for c in str(number) if c.isdigit())
        if not (13 <= len(number) <= 19) or not luhn_ok(number):
            raise ProviderError("That card number isn't valid. Try the test card 4242 4242 4242 4242.")
        try:
            m, y = int(exp_month), int(exp_year)
        except (TypeError, ValueError):
            raise ProviderError("Enter the expiry as MM / YY.")
        if y < 100:
            y += 2000
        today = date.today()
        if not (1 <= m <= 12) or (y, m) < (today.year, today.month):
            raise ProviderError("That card is expired.")
        if not str(cvc).isdigit() or len(str(cvc)) not in (3, 4):
            raise ProviderError("Enter the 3 or 4 digit security code.")
        if number in DECLINE_CARDS:
            raise ProviderError("Card declined (test decline card).")
        brand = card_brand(number)
        # Only brand + last4 are kept. The full number is discarded here.
        return {
            "label": f"{brand.title()} {funding}",
            "brand": brand,
            "last4": number[-4:],
            "external_id": "sim_card_" + secrets.token_hex(8),
        }

    def charge(self, source, cents, description=""):
        return "sim_ch_" + secrets.token_hex(8)

    def payout(self, source, cents, description=""):
        return "sim_po_" + secrets.token_hex(8)


# -------------------------------------------------------------------- plaid

class PlaidProvider:
    name = "plaid"

    def __init__(self, client_id, secret, env="sandbox"):
        self.client_id, self.secret = client_id, secret
        self.base = f"https://{env}.plaid.com"

    @classmethod
    def from_env(cls):
        cid, sec = os.getenv("PLAID_CLIENT_ID"), os.getenv("PLAID_SECRET")
        if cid and sec:
            return cls(cid, sec, os.getenv("PLAID_ENV", "sandbox"))
        return None

    def _post(self, path, body):
        body = {"client_id": self.client_id, "secret": self.secret, **body}
        r = requests.post(self.base + path, json=body, timeout=20)
        data = r.json()
        if r.status_code != 200:
            raise ProviderError(data.get("error_message", "Plaid error"))
        return data

    def create_link_token(self, user_id):
        return self._post("/link/token/create", {
            "user": {"client_user_id": str(user_id)},
            "client_name": "PocketBolt",
            "products": ["auth"],
            "country_codes": ["US"],
            "language": "en",
        })["link_token"]

    def exchange(self, public_token, account_id=None):
        """Exchange the Link public_token and describe the chosen account."""
        access_token = self._post("/item/public_token/exchange", {"public_token": public_token})["access_token"]
        accounts = self._post("/accounts/get", {"access_token": access_token})["accounts"]
        acct = next((a for a in accounts if a["account_id"] == account_id), None) or accounts[0]
        return access_token, acct

    def stripe_bank_token(self, access_token, account_id):
        """Plaid -> Stripe processor handoff for real ACH via Stripe."""
        return self._post("/processor/stripe/bank_account_token/create",
                          {"access_token": access_token, "account_id": account_id})["stripe_bank_account_token"]


# ------------------------------------------------------------------- stripe

class StripeProvider:
    name = "stripe"
    base = "https://api.stripe.com/v1"

    def __init__(self, secret_key, publishable_key):
        self.secret_key, self.publishable_key = secret_key, publishable_key
        if not secret_key.startswith("sk_test_"):
            # Safety: this prototype refuses live keys.
            raise ValueError("PocketBolt prototype only accepts Stripe TEST keys (sk_test_...).")

    @classmethod
    def from_env(cls):
        sk, pk = os.getenv("STRIPE_SECRET_KEY"), os.getenv("STRIPE_PUBLISHABLE_KEY")
        if sk and pk:
            return cls(sk, pk)
        return None

    def _req(self, method, path, data=None):
        r = requests.request(method, self.base + path, data=data, auth=(self.secret_key, ""), timeout=20)
        body = r.json()
        if r.status_code >= 400:
            raise ProviderError(body.get("error", {}).get("message", "Stripe error"))
        return body

    def ensure_customer(self, user):
        if user["stripe_customer_id"]:
            return user["stripe_customer_id"]
        return self._req("POST", "/customers", {"email": user["email"], "name": user["display_name"]})["id"]

    def create_setup_intent(self, customer_id):
        si = self._req("POST", "/setup_intents", {"customer": customer_id, "payment_method_types[]": "card",
                                                 "usage": "off_session"})
        return si["client_secret"]

    def describe_setup_intent(self, setup_intent_id):
        si = self._req("GET", f"/setup_intents/{setup_intent_id}")
        if si["status"] != "succeeded":
            raise ProviderError("Card setup did not complete.")
        pm = self._req("GET", f"/payment_methods/{si['payment_method']}")
        card = pm["card"]
        return {
            "label": f"{card['brand'].title()} {card.get('funding', 'card')}",
            "brand": card["brand"],
            "last4": card["last4"],
            "external_id": pm["id"],
        }

    def charge(self, source, cents, description="", customer_id=None):
        pi = self._req("POST", "/payment_intents", {
            "amount": cents, "currency": "usd", "customer": customer_id,
            "payment_method": source["external_id"], "off_session": "true", "confirm": "true",
            "description": description or "PocketBolt test charge",
        })
        if pi["status"] != "succeeded":
            raise ProviderError(f"Charge not completed (status {pi['status']}).")
        return pi["id"]


def provider_config():
    plaid = PlaidProvider.from_env()
    try:
        stripe = StripeProvider.from_env()
    except ValueError:
        stripe = None
    return plaid, stripe
