"""Core money movement. Every balance change happens inside a single
BEGIN IMMEDIATE transaction so transfers are atomic and balances can never go
negative (enforced both in code and by a CHECK constraint in the schema)."""
import re
from contextlib import contextmanager

MAX_TX_CENTS = 500_000        # $5,000 per transaction (test-mode limit)
MAX_BALANCE_CENTS = 5_000_000  # $50,000 wallet cap


class LedgerError(Exception):
    """A user-facing error (bad amount, insufficient funds, ...)."""


@contextmanager
def atomic(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def parse_amount(value):
    """Turn '12.5', 12.5 or '$1,200.00' into integer cents, validating range."""
    if isinstance(value, bool) or value is None:
        raise LedgerError("Enter an amount.")
    s = str(value).strip().replace("$", "").replace(",", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise LedgerError("Enter a valid dollar amount, like 12.50.")
    whole, _, frac = s.partition(".")
    cents = int(whole) * 100 + int((frac + "00")[:2])
    if cents <= 0:
        raise LedgerError("Amount must be more than $0.00.")
    if cents > MAX_TX_CENTS:
        raise LedgerError("Test-mode limit is $5,000 per transaction.")
    return cents


def fmt(cents):
    return f"${cents / 100:,.2f}"


def _balance(conn, user_id):
    row = conn.execute("SELECT balance_cents FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise LedgerError("User not found.")
    return row["balance_cents"]


def _debit(conn, user_id, cents):
    cur = conn.execute(
        "UPDATE users SET balance_cents = balance_cents - ? WHERE id=? AND balance_cents >= ?",
        (cents, user_id, cents),
    )
    if cur.rowcount != 1:
        raise LedgerError(f"Not enough money in your wallet (balance {fmt(_balance(conn, user_id))}).")


def _credit(conn, user_id, cents):
    if _balance(conn, user_id) + cents > MAX_BALANCE_CENTS:
        raise LedgerError("That would put the wallet over the $50,000 test-mode cap.")
    conn.execute("UPDATE users SET balance_cents = balance_cents + ? WHERE id=?", (cents, user_id))


def transfer(conn, sender_id, recipient_id, cents, note="", funding_source_id=None, provider_ref=None,
             request_id=None):
    """Move money from sender to recipient.

    If funding_source_id is given the money was already pulled from the
    sender's card/bank by the provider, so only the recipient is credited.
    Otherwise it comes from the sender's wallet balance.
    """
    if sender_id == recipient_id:
        raise LedgerError("You can't send money to yourself.")
    if not isinstance(cents, int) or cents <= 0 or cents > MAX_TX_CENTS:
        raise LedgerError("Invalid amount.")
    with atomic(conn):
        if request_id is not None:
            req = conn.execute("SELECT * FROM money_requests WHERE id=?", (request_id,)).fetchone()
            if req is None or req["status"] != "pending":
                raise LedgerError("That request is no longer pending.")
        if funding_source_id is None:
            _debit(conn, sender_id, cents)
        _credit(conn, recipient_id, cents)
        cur = conn.execute(
            "INSERT INTO transactions(type, sender_id, recipient_id, amount_cents, note, funding_source_id, provider_ref)"
            " VALUES('transfer',?,?,?,?,?,?)",
            (sender_id, recipient_id, cents, note[:140], funding_source_id, provider_ref),
        )
        tx_id = cur.lastrowid
        if request_id is not None:
            conn.execute(
                "UPDATE money_requests SET status='paid', transaction_id=?, resolved_at=datetime('now') WHERE id=?",
                (tx_id, request_id),
            )
        return tx_id


def deposit(conn, user_id, cents, funding_source_id, provider_ref=None):
    with atomic(conn):
        _credit(conn, user_id, cents)
        cur = conn.execute(
            "INSERT INTO transactions(type, recipient_id, amount_cents, note, funding_source_id, provider_ref)"
            " VALUES('deposit',?,?,?,?,?)",
            (user_id, cents, "Added cash", funding_source_id, provider_ref),
        )
        return cur.lastrowid


def withdraw(conn, user_id, cents, funding_source_id, provider_ref=None):
    with atomic(conn):
        _debit(conn, user_id, cents)
        cur = conn.execute(
            "INSERT INTO transactions(type, sender_id, amount_cents, note, funding_source_id, provider_ref)"
            " VALUES('withdrawal',?,?,?,?,?)",
            (user_id, cents, "Cashed out", funding_source_id, provider_ref),
        )
        return cur.lastrowid


def create_request(conn, requester_id, payer_id, cents, note=""):
    if requester_id == payer_id:
        raise LedgerError("You can't request money from yourself.")
    with atomic(conn):
        cur = conn.execute(
            "INSERT INTO money_requests(requester_id, payer_id, amount_cents, note) VALUES(?,?,?,?)",
            (requester_id, payer_id, cents, note[:140]),
        )
        return cur.lastrowid


def resolve_request(conn, request_id, user_id, action):
    """Decline (payer) or cancel (requester) a pending request."""
    with atomic(conn):
        req = conn.execute("SELECT * FROM money_requests WHERE id=?", (request_id,)).fetchone()
        if req is None or req["status"] != "pending":
            raise LedgerError("That request is no longer pending.")
        if action == "decline" and req["payer_id"] == user_id:
            status = "declined"
        elif action == "cancel" and req["requester_id"] == user_id:
            status = "cancelled"
        else:
            raise LedgerError("You can't do that to this request.")
        conn.execute(
            "UPDATE money_requests SET status=?, resolved_at=datetime('now') WHERE id=?", (status, request_id)
        )
