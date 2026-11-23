"""PocketBolt - a P2P payments prototype (TEST MODE, no real money)."""
import io
import os
import re
import secrets
import time
from collections import defaultdict
from functools import wraps

import qrcode
import qrcode.image.svg
from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import db
import ledger
from ledger import LedgerError, fmt, parse_amount
from providers import ProviderError, SimulatedProvider, provider_config

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,19}$")


def normalize_phone(p):
    digits = re.sub(r"\D", "", p or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def _secret_key(instance_dir):
    if os.getenv("SECRET_KEY"):
        return os.getenv("SECRET_KEY")
    path = os.path.join(instance_dir, "secret_key")
    if not os.path.exists(path):
        os.makedirs(instance_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    return open(path).read().strip()


def create_app(db_path=None, testing=False):
    instance = os.path.join(HERE, "instance")
    app = Flask(__name__, static_folder=None)
    app.config.update(
        DB_PATH=db_path or os.getenv("POCKETBOLT_DB", os.path.join(instance, "pocketbolt.db")),
        SECRET_KEY=_secret_key(instance),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        TESTING=testing,
    )
    db.init_db(app.config["DB_PATH"])
    sim = SimulatedProvider()
    plaid, stripe = provider_config()
    login_failures = defaultdict(list)

    # ------------------------------------------------------------ helpers
    def conn():
        if "conn" not in g:
            g.conn = db.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    def err(msg, code=400):
        return jsonify({"ok": False, "error": msg}), code

    def body():
        return request.get_json(silent=True) or {}

    def current_user():
        uid = session.get("uid")
        if not uid:
            return None
        return conn().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if u is None:
                return err("Please log in.", 401)
            g.user = u
            return fn(*a, **kw)
        return wrapper

    def public_user(u):
        return {"id": u["id"], "username": u["username"], "display_name": u["display_name"]}

    def me_json(u):
        return {**public_user(u), "email": u["email"], "phone": u["phone"],
                "balance_cents": u["balance_cents"], "balance": fmt(u["balance_cents"])}

    def resolve_recipient(q):
        q = (q or "").strip()
        if not q:
            return None
        c = conn()
        if "@" in q and "." in q.split("@")[-1] and not q.startswith("@"):
            return c.execute("SELECT * FROM users WHERE email=?", (q,)).fetchone()
        phone = normalize_phone(q)
        if phone and not q.lstrip("$@").isalpha():
            row = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            if row:
                return row
        return c.execute("SELECT * FROM users WHERE username=?", (q.lstrip("$@"),)).fetchone()

    def get_source(source_id, user_id):
        if source_id in (None, "", "wallet"):
            return None
        row = conn().execute("SELECT * FROM funding_sources WHERE id=? AND user_id=?",
                             (source_id, user_id)).fetchone()
        if row is None:
            raise LedgerError("Pick one of your linked banks or cards.")
        return row

    def pull_funds(source, cents, description):
        """Charge a linked card/bank. Returns provider reference."""
        if source["provider"] == "stripe" and stripe:
            return stripe.charge(source, cents, description, customer_id=g.user["stripe_customer_id"])
        # Plaid-linked banks: a real ACH debit needs Stripe ACH (processor token)
        # or Plaid Transfer; the prototype simulates the pull.
        return sim.charge(source, cents, description)

    def push_funds(source, cents, description):
        # Real payouts need Stripe Connect / a partner bank; simulated here.
        return sim.payout(source, cents, description)

    def source_json(s):
        return {k: s[k] for k in ("id", "kind", "provider", "label", "brand", "last4", "is_default", "created_at")}

    def add_source(user_id, kind, provider, info):
        c = conn()
        with ledger.atomic(c):
            has_any = c.execute("SELECT 1 FROM funding_sources WHERE user_id=?", (user_id,)).fetchone()
            cur = c.execute(
                "INSERT INTO funding_sources(user_id, kind, provider, label, brand, last4, external_id, is_default)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (user_id, kind, provider, info["label"], info.get("brand"), info["last4"],
                 info.get("external_id"), 0 if has_any else 1),
            )
        row = c.execute("SELECT * FROM funding_sources WHERE id=?", (cur.lastrowid,)).fetchone()
        return source_json(row)

    @app.errorhandler(LedgerError)
    def _ledger_err(e):
        return err(str(e))

    @app.errorhandler(ProviderError)
    def _provider_err(e):
        return err(str(e), 402)

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ static / PWA
    @app.get("/")
    @app.get("/pay/<path:_handle>")
    @app.get("/app/<path:_tab>")
    def index(_handle=None, _tab=None):
        return send_from_directory(STATIC, "index.html")

    @app.get("/sw.js")
    def sw():
        resp = send_from_directory(STATIC, "sw.js", mimetype="application/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(STATIC, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(STATIC, p)

    @app.get("/healthz")
    def health():
        return {"ok": True, "app": "PocketBolt", "mode": "test"}

    # ------------------------------------------------------------ config / auth
    @app.get("/api/config")
    def config():
        return {
            "app": "PocketBolt", "test_mode": True,
            "bank_provider": "plaid" if plaid else "simulated",
            "card_provider": "stripe" if stripe else "simulated",
            "stripe_publishable_key": stripe.publishable_key if stripe else None,
        }

    @app.post("/api/signup")
    def signup():
        b = body()
        username = (b.get("username") or "").strip().lstrip("$@")
        name = (b.get("display_name") or "").strip()[:60]
        email = (b.get("email") or "").strip().lower()
        password = b.get("password") or ""
        phone = normalize_phone(b.get("phone")) if b.get("phone") else None
        if not USERNAME_RE.match(username):
            return err("Pick a $handle of 3-20 letters, numbers or _ that starts with a letter.")
        if not name:
            return err("Enter your name.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return err("Enter a valid email.")
        if b.get("phone") and not phone:
            return err("Enter a 10-digit US phone number.")
        if len(password) < 8:
            return err("Password must be at least 8 characters.")
        c = conn()
        try:
            with ledger.atomic(c):
                cur = c.execute(
                    "INSERT INTO users(username, display_name, email, phone, password_hash) VALUES(?,?,?,?,?)",
                    (username, name, email, phone, generate_password_hash(password)),
                )
        except Exception as e:  # sqlite3.IntegrityError
            if "UNIQUE" in str(e):
                field = "handle" if "username" in str(e) else "email" if "email" in str(e) else "phone"
                return err(f"That {field} is already taken.")
            raise
        session.clear()
        session.permanent = True
        session["uid"] = cur.lastrowid
        return {"ok": True, "user": me_json(current_user())}

    @app.post("/api/login")
    def login():
        b = body()
        ident = (b.get("login") or "").strip().lstrip("$@")
        ip = request.remote_addr or "?"
        now = time.time()
        login_failures[ip] = [t for t in login_failures[ip] if now - t < 300]
        if len(login_failures[ip]) >= 10:
            return err("Too many attempts. Wait a few minutes and try again.", 429)
        u = conn().execute("SELECT * FROM users WHERE username=? OR email=?", (ident, ident.lower())).fetchone()
        if u is None or not check_password_hash(u["password_hash"], b.get("password") or ""):
            login_failures[ip].append(now)
            return err("Wrong handle/email or password.", 401)
        session.clear()
        session.permanent = True
        session["uid"] = u["id"]
        return {"ok": True, "user": me_json(u)}

    @app.post("/api/logout")
    def logout():
        session.clear()
        return {"ok": True}

    @app.get("/api/me")
    @login_required
    def me():
        c = conn()
        pending = c.execute("SELECT COUNT(*) n FROM money_requests WHERE payer_id=? AND status='pending'",
                            (g.user["id"],)).fetchone()["n"]
        return {"ok": True, "user": me_json(g.user), "pending_requests": pending}

    @app.get("/api/users/search")
    @login_required
    def search_users():
        q = (request.args.get("q") or "").strip().lstrip("$@")
        if len(q) < 1:
            return {"ok": True, "users": []}
        exact = resolve_recipient(request.args.get("q"))
        like = f"%{q}%"
        rows = conn().execute(
            "SELECT * FROM users WHERE id != ? AND (username LIKE ? OR display_name LIKE ?) ORDER BY username LIMIT 8",
            (g.user["id"], like, like),
        ).fetchall()
        users = [public_user(r) for r in rows]
        if exact and exact["id"] != g.user["id"] and all(u["id"] != exact["id"] for u in users):
            users.insert(0, public_user(exact))
        return {"ok": True, "users": users}

    # ------------------------------------------------------------ funding sources
    @app.get("/api/funding_sources")
    @login_required
    def list_sources():
        rows = conn().execute("SELECT * FROM funding_sources WHERE user_id=? ORDER BY is_default DESC, id",
                              (g.user["id"],)).fetchall()
        return {"ok": True, "sources": [source_json(r) for r in rows]}

    @app.delete("/api/funding_sources/<int:sid>")
    @login_required
    def remove_source(sid):
        c = conn()
        with ledger.atomic(c):
            cur = c.execute("DELETE FROM funding_sources WHERE id=? AND user_id=?", (sid, g.user["id"]))
            if cur.rowcount != 1:
                raise LedgerError("Not found.")
            c.execute("UPDATE funding_sources SET is_default=1 WHERE id=(SELECT id FROM funding_sources "
                      "WHERE user_id=? ORDER BY id LIMIT 1) AND NOT EXISTS(SELECT 1 FROM funding_sources "
                      "WHERE user_id=? AND is_default=1)", (g.user["id"], g.user["id"]))
        return {"ok": True}

    @app.post("/api/funding_sources/<int:sid>/default")
    @login_required
    def default_source(sid):
        c = conn()
        get_source(sid, g.user["id"])
        with ledger.atomic(c):
            c.execute("UPDATE funding_sources SET is_default=(id=?) WHERE user_id=?", (sid, g.user["id"]))
        return {"ok": True}

    @app.get("/api/banks")
    @login_required
    def banks():
        return {"ok": True, "banks": sim.banks()}

    @app.post("/api/link/bank")
    @login_required
    def link_bank_sim():
        b = body()
        info = sim.link_bank(b.get("institution_id"), b.get("username"), b.get("password"),
                             b.get("account_type") or "checking")
        return {"ok": True, "source": add_source(g.user["id"], "bank", "simulated", info)}

    @app.post("/api/link/card")
    @login_required
    def link_card_sim():
        b = body()
        info = sim.link_card(b.get("number"), b.get("exp_month"), b.get("exp_year"), b.get("cvc"),
                             b.get("funding") or "debit")
        return {"ok": True, "source": add_source(g.user["id"], "card", "simulated", info)}

    @app.post("/api/plaid/link_token")
    @login_required
    def plaid_link_token():
        if not plaid:
            return err("Plaid keys are not configured; using the built-in sandbox.", 404)
        return {"ok": True, "link_token": plaid.create_link_token(g.user["id"])}

    @app.post("/api/plaid/exchange")
    @login_required
    def plaid_exchange():
        if not plaid:
            return err("Plaid is not configured.", 404)
        b = body()
        access_token, acct = plaid.exchange(b.get("public_token"), b.get("account_id"))
        inst = (b.get("institution") or {}).get("name") or "Bank"
        info = {"label": f"{inst} {acct.get('subtype') or 'account'}".strip(), "brand": inst,
                "last4": acct.get("mask") or "0000", "external_id": f"{access_token}|{acct['account_id']}"}
        # NOTE: in production, encrypt access tokens at rest (KMS) instead of storing plain text.
        return {"ok": True, "source": add_source(g.user["id"], "bank", "plaid", info)}

    @app.post("/api/stripe/setup_intent")
    @login_required
    def stripe_setup():
        if not stripe:
            return err("Stripe keys are not configured; using the built-in sandbox.", 404)
        cid = stripe.ensure_customer(g.user)
        c = conn()
        with ledger.atomic(c):
            c.execute("UPDATE users SET stripe_customer_id=? WHERE id=?", (cid, g.user["id"]))
        return {"ok": True, "client_secret": stripe.create_setup_intent(cid)}

    @app.post("/api/stripe/confirm")
    @login_required
    def stripe_confirm():
        if not stripe:
            return err("Stripe is not configured.", 404)
        info = stripe.describe_setup_intent(body().get("setup_intent_id"))
        return {"ok": True, "source": add_source(g.user["id"], "card", "stripe", info)}

    # ------------------------------------------------------------ money
    @app.post("/api/send")
    @login_required
    def send():
        b = body()
        cents = parse_amount(b.get("amount"))
        to = resolve_recipient(b.get("to"))
        if to is None:
            raise LedgerError("No PocketBolt user matches that $handle, phone or email.")
        if to["id"] == g.user["id"]:
            raise LedgerError("You can't send money to yourself.")
        source = get_source(b.get("source_id"), g.user["id"])
        ref = None
        if source is not None:
            ref = pull_funds(source, cents, f"PocketBolt payment to ${to['username']}")
        tx = ledger.transfer(conn(), g.user["id"], to["id"], cents, (b.get("note") or "").strip(),
                             source["id"] if source else None, ref)
        return {"ok": True, "transaction_id": tx, "message": f"Sent {fmt(cents)} to ${to['username']}",
                "user": me_json(current_user())}

    @app.post("/api/request")
    @login_required
    def request_money():
        b = body()
        cents = parse_amount(b.get("amount"))
        payer = resolve_recipient(b.get("from"))
        if payer is None:
            raise LedgerError("No PocketBolt user matches that $handle, phone or email.")
        rid = ledger.create_request(conn(), g.user["id"], payer["id"], cents, (b.get("note") or "").strip())
        return {"ok": True, "request_id": rid, "message": f"Requested {fmt(cents)} from ${payer['username']}"}

    def request_json(r):
        return {"id": r["id"], "amount_cents": r["amount_cents"], "amount": fmt(r["amount_cents"]),
                "note": r["note"], "status": r["status"], "created_at": r["created_at"],
                "requester": {"username": r["r_user"], "display_name": r["r_name"]},
                "payer": {"username": r["p_user"], "display_name": r["p_name"]},
                "incoming": r["payer_id"] == g.user["id"]}

    @app.get("/api/requests")
    @login_required
    def list_requests():
        rows = conn().execute(
            "SELECT m.*, ru.username r_user, ru.display_name r_name, pu.username p_user, pu.display_name p_name "
            "FROM money_requests m JOIN users ru ON ru.id=m.requester_id JOIN users pu ON pu.id=m.payer_id "
            "WHERE (m.payer_id=? OR m.requester_id=?) AND m.status='pending' ORDER BY m.id DESC",
            (g.user["id"], g.user["id"]),
        ).fetchall()
        return {"ok": True, "requests": [request_json(r) for r in rows]}

    @app.post("/api/requests/<int:rid>/pay")
    @login_required
    def pay_request(rid):
        c = conn()
        r = c.execute("SELECT * FROM money_requests WHERE id=?", (rid,)).fetchone()
        if r is None or r["payer_id"] != g.user["id"]:
            return err("Request not found.", 404)
        if r["status"] != "pending":
            raise LedgerError("That request is no longer pending.")
        source = get_source(body().get("source_id"), g.user["id"])
        ref = pull_funds(source, r["amount_cents"], "PocketBolt request") if source else None
        tx = ledger.transfer(c, g.user["id"], r["requester_id"], r["amount_cents"], r["note"],
                             source["id"] if source else None, ref, request_id=rid)
        return {"ok": True, "transaction_id": tx, "message": f"Paid {fmt(r['amount_cents'])}",
                "user": me_json(current_user())}

    @app.post("/api/requests/<int:rid>/<action>")
    @login_required
    def resolve(rid, action):
        if action not in ("decline", "cancel"):
            return err("Unknown action.", 404)
        ledger.resolve_request(conn(), rid, g.user["id"], action)
        return {"ok": True}

    @app.post("/api/add_funds")
    @login_required
    def add_funds():
        b = body()
        cents = parse_amount(b.get("amount"))
        source = get_source(b.get("source_id"), g.user["id"])
        if source is None:
            raise LedgerError("Link a bank or card first.")
        ref = pull_funds(source, cents, "PocketBolt add cash")
        ledger.deposit(conn(), g.user["id"], cents, source["id"], ref)
        return {"ok": True, "message": f"Added {fmt(cents)} from {source['label']} ••{source['last4']}",
                "user": me_json(current_user())}

    @app.post("/api/cash_out")
    @login_required
    def cash_out():
        b = body()
        cents = parse_amount(b.get("amount"))
        source = get_source(b.get("source_id"), g.user["id"])
        if source is None:
            raise LedgerError("Link a bank or debit card to cash out to.")
        # Debit the wallet first (atomic, can't overdraw), then push.
        tx = ledger.withdraw(conn(), g.user["id"], cents, source["id"])
        ref = push_funds(source, cents, "PocketBolt cash out")
        c = conn()
        with ledger.atomic(c):
            c.execute("UPDATE transactions SET provider_ref=? WHERE id=?", (ref, tx))
        return {"ok": True, "message": f"Cashed out {fmt(cents)} to {source['label']} ••{source['last4']}",
                "user": me_json(current_user())}

    @app.get("/api/activity")
    @login_required
    def activity():
        uid = g.user["id"]
        rows = conn().execute(
            "SELECT t.*, s.username s_user, s.display_name s_name, r.username r_user, r.display_name r_name, "
            "f.label f_label, f.last4 f_last4 FROM transactions t "
            "LEFT JOIN users s ON s.id=t.sender_id LEFT JOIN users r ON r.id=t.recipient_id "
            "LEFT JOIN funding_sources f ON f.id=t.funding_source_id "
            "WHERE t.sender_id=? OR t.recipient_id=? ORDER BY t.id DESC LIMIT 100",
            (uid, uid),
        ).fetchall()
        items = []
        for t in rows:
            outgoing = t["sender_id"] == uid
            if t["type"] == "transfer":
                other = (t["r_user"], t["r_name"]) if outgoing else (t["s_user"], t["s_name"])
                title = f"{'To' if outgoing else 'From'} {other[1]}"
                sub = f"${other[0]}"
            elif t["type"] == "deposit":
                title, sub = "Added cash", f"{t['f_label'] or 'Linked account'} ••{t['f_last4'] or ''}"
            else:
                title, sub = "Cash out", f"{t['f_label'] or 'Linked account'} ••{t['f_last4'] or ''}"
            items.append({"id": t["id"], "type": t["type"], "title": title, "subtitle": sub, "note": t["note"],
                          "direction": "out" if outgoing else "in", "amount_cents": t["amount_cents"],
                          "amount": fmt(t["amount_cents"]), "created_at": t["created_at"],
                          "status": t["status"]})
        return {"ok": True, "items": items}

    @app.get("/api/qr.svg")
    def qr_svg():
        data = (request.args.get("data") or "")[:300]
        if not data:
            return err("data required")
        img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), mimetype="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})

    return app


app = create_app() if os.getenv("POCKETBOLT_NO_AUTOAPP") != "1" else None

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
