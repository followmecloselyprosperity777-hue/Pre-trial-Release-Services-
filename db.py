"""SQLite storage for PocketBolt. All money is stored as integer cents."""
import os
import sqlite3

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone         TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    stripe_customer_id TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS funding_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('bank','card')),
    provider    TEXT NOT NULL,              -- simulated | plaid | stripe
    label       TEXT NOT NULL,              -- e.g. "Chase Checking" / "Visa debit"
    brand       TEXT,                       -- visa/mastercard or institution name
    last4       TEXT NOT NULL,
    external_id TEXT,                       -- provider token/id (never a raw card number)
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type           TEXT NOT NULL CHECK (type IN ('transfer','deposit','withdrawal')),
    sender_id      INTEGER REFERENCES users(id),
    recipient_id   INTEGER REFERENCES users(id),
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    note           TEXT NOT NULL DEFAULT '',
    funding_source_id INTEGER REFERENCES funding_sources(id),
    provider_ref   TEXT,
    status         TEXT NOT NULL DEFAULT 'completed',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS money_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_id  INTEGER NOT NULL REFERENCES users(id),
    payer_id      INTEGER NOT NULL REFERENCES users(id),
    amount_cents  INTEGER NOT NULL CHECK (amount_cents > 0),
    note          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','paid','declined','cancelled')),
    transaction_id INTEGER REFERENCES transactions(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);

CREATE INDEX IF NOT EXISTS ix_tx_sender ON transactions(sender_id);
CREATE INDEX IF NOT EXISTS ix_tx_recipient ON transactions(recipient_id);
CREATE INDEX IF NOT EXISTS ix_req_payer ON money_requests(payer_id, status);
CREATE INDEX IF NOT EXISTS ix_req_requester ON money_requests(requester_id, status);
"""


def connect(path):
    # isolation_level=None -> we control transactions explicitly (BEGIN IMMEDIATE)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(path):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.close()
