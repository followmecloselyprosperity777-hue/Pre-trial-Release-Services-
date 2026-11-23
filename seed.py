"""Create demo users (password for all: demo1234). Safe to run repeatedly."""
import os
os.environ["POCKETBOLT_NO_AUTOAPP"] = "1"

from werkzeug.security import generate_password_hash  # noqa: E402

import db  # noqa: E402
import ledger  # noqa: E402
from app import create_app  # noqa: E402

DEMO = [
    ("denzel", "Denzel Haywood", "denzel@demo.pocketbolt.test", "5550100001", 25000),
    ("maya", "Maya Brooks", "maya@demo.pocketbolt.test", "5550100002", 12000),
    ("jordan", "Jordan Reyes", "jordan@demo.pocketbolt.test", "5550100003", 8000),
]


def seed(db_path):
    conn = db.connect(db_path)
    ids = {}
    for username, name, email, phone, start in DEMO:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            ids[username] = row["id"]
            continue
        with ledger.atomic(conn):
            uid = conn.execute(
                "INSERT INTO users(username, display_name, email, phone, password_hash) VALUES(?,?,?,?,?)",
                (username, name, email, phone, generate_password_hash("demo1234")),
            ).lastrowid
            card = conn.execute(
                "INSERT INTO funding_sources(user_id, kind, provider, label, brand, last4, external_id, is_default)"
                " VALUES(?, 'card', 'simulated', 'Visa debit', 'visa', '4242', 'sim_card_seed', 1)", (uid,)
            ).lastrowid
            conn.execute(
                "INSERT INTO funding_sources(user_id, kind, provider, label, brand, last4, external_id)"
                " VALUES(?, 'bank', 'simulated', 'Chase Checking', 'Chase (sandbox)', '6789', 'sim_bank_seed')",
                (uid,),
            )
        ledger.deposit(conn, uid, start, card, "seed")
        ids[username] = uid
        print(f"created ${username}")
    if not conn.execute("SELECT 1 FROM transactions WHERE type='transfer'").fetchone():
        ledger.transfer(conn, ids["maya"], ids["denzel"], 2500, "Pizza night 🍕")
        ledger.transfer(conn, ids["denzel"], ids["jordan"], 1500, "Uber split")
        ledger.create_request(conn, ids["jordan"], ids["denzel"], 2000, "Concert tickets 🎟️")
        ledger.create_request(conn, ids["denzel"], ids["maya"], 1200, "Coffee run")
    conn.close()


if __name__ == "__main__":
    app = create_app()
    seed(app.config["DB_PATH"])
    print("Demo logins: denzel / maya / jordan  (password demo1234)")
