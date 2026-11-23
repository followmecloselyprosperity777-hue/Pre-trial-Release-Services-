import threading

import pytest

import db
import ledger
from ledger import LedgerError, parse_amount


def bal(client):
    return client.get("/api/me").json["user"]["balance_cents"]


def total_money(path):
    c = db.connect(path)
    v = c.execute("SELECT SUM(balance_cents) s FROM users").fetchone()["s"]
    c.close()
    return v


# ---------------------------------------------------------------- amounts
@pytest.mark.parametrize("raw,cents", [("1", 100), ("12.5", 1250), ("$1,000.01", 100001), ("0.01", 1)])
def test_parse_amount_ok(raw, cents):
    assert parse_amount(raw) == cents


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.234", "", None, "5000.01", True, "1e3"])
def test_parse_amount_rejects(raw):
    with pytest.raises(LedgerError):
        parse_amount(raw)


# ---------------------------------------------------------------- auth
def test_passwords_are_hashed(app, clients, db_path):
    c = db.connect(db_path)
    h = c.execute("SELECT password_hash FROM users WHERE username='alice'").fetchone()[0]
    assert "password123" not in h and h.startswith(("scrypt:", "pbkdf2:"))


def test_login_and_protected_routes(app, clients):
    anon = app.test_client()
    assert anon.get("/api/me").status_code == 401
    assert anon.post("/api/login", json={"login": "alice", "password": "nope"}).status_code == 401
    assert anon.post("/api/login", json={"login": "alice", "password": "password123"}).status_code == 200
    assert anon.get("/api/me").status_code == 200


# ---------------------------------------------------------------- transfers
def test_send_moves_money(clients):
    alice, bob = clients
    r = alice.post("/api/send", json={"to": "$bob", "amount": "40", "note": "lunch"})
    assert r.status_code == 200, r.json
    assert bal(alice) == 6000 and bal(bob) == 4000


def test_send_by_email_and_phone(clients):
    alice, bob = clients
    assert alice.post("/api/send", json={"to": "bob@t.test", "amount": "1"}).status_code == 200
    phone = bob.get("/api/me").json["user"]["phone"]
    assert alice.post("/api/send", json={"to": f"({phone[:3]}) {phone[3:6]}-{phone[6:]}", "amount": "1"}).status_code == 200
    assert bal(bob) == 200


def test_cannot_overdraw(clients):
    alice, bob = clients
    r = alice.post("/api/send", json={"to": "bob", "amount": "100.01"})
    assert r.status_code == 400 and "Not enough" in r.json["error"]
    assert bal(alice) == 10000 and bal(bob) == 0
    assert bob.post("/api/send", json={"to": "alice", "amount": "0.01"}).status_code == 400
    assert bob.post("/api/cash_out", json={"amount": "1", "source_id": None}).status_code == 400


def test_cannot_pay_self_or_unknown(clients):
    alice, _ = clients
    assert alice.post("/api/send", json={"to": "alice", "amount": "1"}).status_code == 400
    assert alice.post("/api/send", json={"to": "ghost", "amount": "1"}).status_code == 400


def test_send_from_linked_card_does_not_touch_balance(clients):
    alice, bob = clients
    src = bob.post("/api/link/card", json={"number": "5555555555554444", "exp_month": 1, "exp_year": 2099,
                                           "cvc": "123"}).json["source"]
    r = bob.post("/api/send", json={"to": "alice", "amount": "5", "source_id": src["id"]})
    assert r.status_code == 200
    assert bal(bob) == 0 and bal(alice) == 10500


def test_cannot_use_someone_elses_card(clients):
    alice, bob = clients
    src = alice.get("/api/funding_sources").json["sources"][0]
    r = bob.post("/api/send", json={"to": "alice", "amount": "5", "source_id": src["id"]})
    assert r.status_code == 400


def test_failed_transfer_is_rolled_back(db_path, clients):
    """If any step inside the transaction fails, nothing is applied."""
    conn = db.connect(db_path)
    before = total_money(db_path)
    with pytest.raises(LedgerError):
        ledger.transfer(conn, 1, 999, 500)  # recipient doesn't exist -> credit fails after debit
    assert total_money(db_path) == before
    assert conn.execute("SELECT balance_cents FROM users WHERE id=1").fetchone()[0] == 10000
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE type='transfer'").fetchone()[0] == 0


def test_db_constraint_blocks_negative_balance(db_path, clients):
    conn = db.connect(db_path)
    with pytest.raises(Exception):
        conn.execute("UPDATE users SET balance_cents = -1 WHERE id=1")


def test_concurrent_transfers_never_overdraw(db_path, clients):
    """50 threads each try to send $5 from a $100 wallet: exactly 20 succeed."""
    results = []

    def worker(i):
        conn = db.connect(db_path)
        try:
            ledger.transfer(conn, 1, 2, 500, f"t{i}")
            results.append("ok")
        except LedgerError:
            results.append("fail")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    conn = db.connect(db_path)
    a = conn.execute("SELECT balance_cents FROM users WHERE id=1").fetchone()[0]
    b = conn.execute("SELECT balance_cents FROM users WHERE id=2").fetchone()[0]
    assert results.count("ok") == 20 and a == 0 and b == 10000
    assert total_money(db_path) == 10000


def test_concurrent_back_and_forth_conserves_money(db_path, clients):
    def worker(src, dst):
        conn = db.connect(db_path)
        for _ in range(30):
            try:
                ledger.transfer(conn, src, dst, 700)
            except LedgerError:
                pass
        conn.close()

    ts = [threading.Thread(target=worker, args=(1, 2)), threading.Thread(target=worker, args=(2, 1))] * 1
    ts += [threading.Thread(target=worker, args=(1, 2)), threading.Thread(target=worker, args=(2, 1))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    conn = db.connect(db_path)
    rows = conn.execute("SELECT balance_cents FROM users").fetchall()
    assert all(r[0] >= 0 for r in rows)
    assert total_money(db_path) == 10000


# ---------------------------------------------------------------- requests
def test_request_accept_flow(clients):
    alice, bob = clients
    rid = bob.post("/api/request", json={"from": "alice", "amount": "25", "note": "tickets"}).json["request_id"]
    incoming = alice.get("/api/requests").json["requests"]
    assert incoming[0]["incoming"] and incoming[0]["amount_cents"] == 2500
    assert bob.post(f"/api/requests/{rid}/pay", json={}).status_code == 404  # requester can't pay own request
    assert alice.post(f"/api/requests/{rid}/pay", json={}).status_code == 200
    assert bal(alice) == 7500 and bal(bob) == 2500
    # paying twice is impossible
    assert alice.post(f"/api/requests/{rid}/pay", json={}).status_code == 400
    assert bal(alice) == 7500


def test_request_decline_and_cancel(clients):
    alice, bob = clients
    r1 = bob.post("/api/request", json={"from": "alice", "amount": "5"}).json["request_id"]
    r2 = bob.post("/api/request", json={"from": "alice", "amount": "6"}).json["request_id"]
    assert bob.post(f"/api/requests/{r1}/decline", json={}).status_code == 400  # only payer declines
    assert alice.post(f"/api/requests/{r1}/decline", json={}).status_code == 200
    assert alice.post(f"/api/requests/{r2}/cancel", json={}).status_code == 400  # only requester cancels
    assert bob.post(f"/api/requests/{r2}/cancel", json={}).status_code == 200
    assert alice.post(f"/api/requests/{r1}/pay", json={}).status_code == 400
    assert alice.get("/api/requests").json["requests"] == []
    assert bal(alice) == 10000


def test_request_unaffordable_stays_pending(clients):
    alice, bob = clients
    rid = alice.post("/api/request", json={"from": "bob", "amount": "10"}).json["request_id"]
    assert bob.post(f"/api/requests/{rid}/pay", json={}).status_code == 400
    assert bob.get("/api/requests").json["requests"][0]["status"] == "pending"


# ---------------------------------------------------------------- funding
def test_add_and_cash_out(clients):
    alice, _ = clients
    src = alice.get("/api/funding_sources").json["sources"][0]
    assert alice.post("/api/cash_out", json={"amount": "30", "source_id": src["id"]}).status_code == 200
    assert bal(alice) == 7000
    assert alice.post("/api/cash_out", json={"amount": "70.01", "source_id": src["id"]}).status_code == 400
    assert bal(alice) == 7000
    kinds = [i["type"] for i in alice.get("/api/activity").json["items"]]
    assert kinds[:2] == ["withdrawal", "deposit"]


def test_card_validation(clients):
    _, bob = clients
    bad = bob.post("/api/link/card", json={"number": "4242424242424241", "exp_month": 1, "exp_year": 2099, "cvc": "1"})
    assert bad.status_code == 402
    declined = bob.post("/api/link/card", json={"number": "4000000000000002", "exp_month": 1, "exp_year": 2099,
                                                "cvc": "123"})
    assert declined.status_code == 402 and "declined" in declined.json["error"]
    ok = bob.post("/api/link/card", json={"number": "4242 4242 4242 4242", "exp_month": "12", "exp_year": "99",
                                          "cvc": "123"})
    assert ok.status_code == 200 and ok.json["source"]["last4"] == "4242"


def test_card_number_never_stored(db_path, clients):
    conn = db.connect(db_path)
    dump = "\n".join(conn.iterdump())
    assert "4242424242424242" not in dump


def test_bank_link_sandbox(clients):
    _, bob = clients
    bad = bob.post("/api/link/bank", json={"institution_id": "ins_sim_chase", "username": "x", "password": "y"})
    assert bad.status_code == 402
    ok = bob.post("/api/link/bank", json={"institution_id": "ins_sim_chase", "username": "user_good",
                                          "password": "pass_good"})
    assert ok.status_code == 200 and ok.json["source"]["kind"] == "bank"


def test_pwa_assets(app):
    c = app.test_client()
    assert c.get("/").status_code == 200
    assert c.get("/manifest.webmanifest").json["display"] == "standalone"
    assert c.get("/sw.js").status_code == 200
    assert c.get("/pay/alice").status_code == 200
    assert c.get("/api/qr.svg?data=hello").mimetype == "image/svg+xml"
    assert c.get("/api/config").json["test_mode"] is True
