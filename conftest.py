import os
import sys

os.environ["POCKETBOLT_NO_AUTOAPP"] = "1"
for k in ("PLAID_CLIENT_ID", "PLAID_SECRET", "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY"):
    os.environ.pop(k, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import db  # noqa: E402
from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "test.db"), testing=True)


@pytest.fixture
def db_path(app):
    return app.config["DB_PATH"]


def make_client(app, username, balance_dollars=0):
    c = app.test_client()
    r = c.post("/api/signup", json={"username": username, "display_name": username.title(),
                                    "email": f"{username}@t.test", "password": "password123",
                                    "phone": f"555{abs(hash(username)) % 10**7:07d}"})
    assert r.status_code == 200, r.json
    if balance_dollars:
        src = c.post("/api/link/card", json={"number": "4242424242424242", "exp_month": 12,
                                             "exp_year": 2099, "cvc": "123"}).json["source"]
        r = c.post("/api/add_funds", json={"amount": str(balance_dollars), "source_id": src["id"]})
        assert r.status_code == 200, r.json
    return c


@pytest.fixture
def clients(app):
    return make_client(app, "alice", 100), make_client(app, "bob", 0)
