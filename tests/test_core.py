import asyncio

import pytest
from fastapi.testclient import TestClient

from sniper import scoring
from sniper.app import build_app
from sniper.db import DB
from sniper.matching import match, normalize
from sniper.models import Listing, Watch
from sniper.sources.base import Source
from sniper.watcher import Watcher


def L(title, price=280.0, **kw):
    kw.setdefault("source_id", title)
    return Listing(source="Test", title=title, url="https://x/" + title, price=price, **kw)


W = Watch(query="RTX 4060")


@pytest.mark.parametrize("title,ok", [
    ("MSI GeForce RTX 4060 Ventus 2X 8GB", True),
    ("ASUS Dual RTX4060 8 GB OC", True),
    ("Nvidia RTX 4060 Founders Edition FE", True),
    ("MSI RTX 4060 Ti 8GB", False),
    ("Zotac RTX 4060Ti", False),
    ("RTX 4060 Super", False),
    ("RTX 4070 12GB", False),
    ("RTX 4060 empty box only", False),
    ("RTX 4060 for parts not working", False),
])
def test_match_titles(title, ok):
    assert match(L(title), W)[0] is ok


def test_normalize_glues_units():
    assert normalize("RTX4060 8 GB") == "rtx 4060 8gb"


def test_query_with_variant_allows_it():
    assert match(L("MSI RTX 4060 Ti 16GB"), Watch(query="RTX 4060 Ti"))[0]


def test_required_and_excluded_terms():
    w = Watch(query="RTX 4060", required_terms=["8gb"], excluded_terms=["laptop"])
    assert match(L("RTX 4060 8GB"), w)[0]
    assert not match(L("RTX 4060"), w)[0]
    assert not match(L("Gaming Laptop RTX 4060 8GB"), w)[0]


def test_condition_radius_and_auction_filters():
    w = Watch(query="RTX 4060", conditions=["new"], radius_miles=25)
    assert not match(L("RTX 4060", condition="used"), w)[0]
    assert not match(L("RTX 4060", condition="new", local_pickup=True, distance_miles=40), w)[0]
    assert not match(L("RTX 4060", condition="new", is_auction=True), w)[0]
    assert match(L("RTX 4060", condition="new", local_pickup=True, distance_miles=10), w)[0]


def test_total_cost_shipping_tax():
    w = Watch(query="x", tax_rate=0.1)
    assert scoring.total_cost(L("a", 100, shipping=10), w) == (120.0, 10.0, False)
    assert scoring.total_cost(L("a", 100), w)[2] is True       # unknown shipping estimated
    assert scoring.total_cost(L("a", 100, local_pickup=True, taxable=False), w) == (100.0, 0.0, False)


def test_scam_detection_and_deal_label():
    medians = {"new": (300.0, 10), "used": (250.0, 10)}
    scam = scoring.score(L("RTX 4060 sealed", 120, condition="new", local_pickup=True, taxable=False,
                           seller_feedback_count=0, description="zelle only"), W, medians)
    assert scam.risk_score >= scoring.RISK_HIDE_THRESHOLD
    assert scam.deal["label"] == "suspicious"
    good = scoring.score(L("RTX 4060", 240, condition="new", shipping=0, seller_feedback_count=500,
                           seller_rating=99.8), W, medians)
    assert good.risk_score == 0 and good.deal["label"] == "great"


def test_best_value_prefers_close_new_over_far_used_at_similar_price():
    m = {}
    new = scoring.score(L("a", 280, condition="new", shipping=0), W, m)
    used_far = scoring.score(L("b", 260, condition="used", local_pickup=True, distance_miles=50, taxable=False), W, m)
    assert new.effective_cost < used_far.effective_cost


class FakeSource(Source):
    key, name = "fake", "Fake"

    def __init__(self):
        self.items = []

    def configured(self):
        return True

    async def search(self, watch, client):
        return list(self.items)


def test_watcher_alerts_on_new_lowest(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    src = FakeSource()
    src.items = [L("RTX 4060 a", 300, shipping=0, condition="new"), L("RTX 4060 b", 310, shipping=0, condition="new")]
    w = db.create_watch(Watch(query="RTX 4060", max_price=250))
    watcher = Watcher(db, sources=[src])

    asyncio.run(watcher.check(w))
    assert db.list_alerts() == []                       # nothing under max on first run

    src.items.append(L("RTX 4060 c", 295, shipping=0, condition="new"))
    asyncio.run(watcher.check(db.get_watch(w.id)))
    alerts = db.list_alerts()
    assert len(alerts) == 1 and "new lowest" in alerts[0]["reason"]

    asyncio.run(watcher.check(db.get_watch(w.id)))     # nothing changed -> no repeat alert
    assert len(db.list_alerts()) == 1

    src.items[0] = L("RTX 4060 a", 240, shipping=0, condition="new")   # price cut under max
    asyncio.run(watcher.check(db.get_watch(w.id)))
    assert len(db.list_alerts()) == 2


def _client(tmp_path, monkeypatch, name="api.db"):
    monkeypatch.setenv("SNIPER_DEMO", "1")
    monkeypatch.setenv("SNIPER_SECRET_FILE", str(tmp_path / "secret"))
    return TestClient(build_app(db=DB(str(tmp_path / name)), background=False))


def _signup(client, email="a@example.com"):
    r = client.post("/api/auth/signup", json={"email": email, "password": "hunter2hunter2"})
    assert r.status_code == 200, r.text
    return r


def test_api_end_to_end(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/watches").status_code == 401
    _signup(client)
    r = client.post("/api/watches", json={"query": "RTX 4060", "max_price": 290, "zip_code": "78701", "radius_miles": 40})
    assert r.status_code == 200
    wid = r.json()["id"]
    res = client.get(f"/api/watches/{wid}/listings").json()
    items = res["items"]
    assert items and all("ti" not in i["title"].lower().split() for i in items)
    assert items == sorted(items, key=lambda i: i["total_cost"])
    assert sum(res["sites"].values()) == len(items)

    # site filter: only that site's listings come back, counts for every site stay
    site = next(iter(res["sites"]))
    only = client.get(f"/api/watches/{wid}/listings", params={"site": site}).json()
    assert only["items"] and all(i["site"] == site for i in only["items"])
    assert only["sites"] == res["sites"]

    assert client.get(f"/api/watches/{wid}/listings?sort=value").status_code == 200
    client.post(f"/api/watches/{wid}/check")
    h = client.get(f"/api/watches/{wid}/history").json()
    assert len(h["snapshots"]) >= 2
    assert client.delete(f"/api/watches/{wid}").json() == {"ok": True}


def test_users_are_isolated(tmp_path, monkeypatch):
    a = _client(tmp_path, monkeypatch)
    _signup(a, "a@example.com")
    wid = a.post("/api/watches", json={"query": "RTX 4060"}).json()["id"]

    b = TestClient(a.app)          # separate cookie jar = separate browser
    _signup(b, "b@example.com")
    assert b.get("/api/watches").json() == []
    for method, path in [("get", f"/api/watches/{wid}/listings"), ("get", f"/api/watches/{wid}/history"),
                         ("post", f"/api/watches/{wid}/check"), ("delete", f"/api/watches/{wid}")]:
        assert getattr(b, method)(path).status_code == 404
    assert b.get("/api/alerts").json() == []
    assert len(a.get("/api/watches").json()) == 1


def test_auth_rules(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _signup(c, "a@example.com")
    assert c.post("/api/auth/signup", json={"email": "A@example.com", "password": "hunter2hunter2"}).status_code == 409
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/auth/login", json={"email": "a@example.com", "password": "wrongwrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "a@example.com", "password": "hunter2hunter2"}).status_code == 200
    assert c.get("/api/me").json()["email"] == "a@example.com"

    assert c.put("/api/me", json={"ntfy_topic": "bad topic!"}).status_code == 422
    assert c.put("/api/me", json={"discord_webhook": "http://169.254.169.254/x"}).status_code == 422
    assert c.put("/api/me", json={"ntfy_topic": "sniper-abc123"}).json()["ntfy_topic"] == "sniper-abc123"

    monkeypatch.setenv("SNIPER_INVITE_CODE", "letmein")
    d = TestClient(c.app)
    assert d.post("/api/auth/signup", json={"email": "z@example.com", "password": "hunter2hunter2"}).status_code == 403
    assert d.post("/api/auth/signup", json={"email": "z@example.com", "password": "hunter2hunter2",
                                            "invite_code": "letmein"}).status_code == 200


def test_gmail_token_encrypted_at_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_SECRET_FILE", str(tmp_path / "secret"))
    db = DB(str(tmp_path / "g.db"))
    u = db.create_user("a@example.com", "hunter2hunter2")
    db.set_gmail(u.id, "a@gmail.com", "1//refresh-token-value")
    raw = db.conn.execute("SELECT gmail_token FROM users").fetchone()[0]
    assert "refresh-token-value" not in raw
    assert db.get_gmail_token(u.id) == "1//refresh-token-value"


def test_maintenance_notice(tmp_path, monkeypatch):
    from sniper import maintenance
    c = _client(tmp_path, monkeypatch)
    db = c.app.state.db
    first = c.get("/api/notice").json()
    assert first["maintenance"] is None and first["boot_id"]

    maintenance.announce(db, 60, "brb")
    n = c.get("/api/notice").json()["maintenance"]       # visible without signing in
    assert n["message"] == "brb" and 55 <= n["seconds_left"] <= 60

    maintenance.cancel(db)
    assert c.get("/api/notice").json()["maintenance"] is None

    # admin HTTP endpoint: off without a token, needs the right token when on
    assert c.post("/api/admin/maintenance", json={"seconds": 60}).status_code == 403
    monkeypatch.setenv("SNIPER_ADMIN_TOKEN", "s3cret-token")
    assert c.post("/api/admin/maintenance", json={"seconds": 60},
                  headers={"Authorization": "Bearer nope"}).status_code == 403
    assert c.post("/api/admin/maintenance", json={"seconds": 60},
                  headers={"Authorization": "Bearer s3cret-token"}).status_code == 200
    assert c.get("/api/notice").json()["maintenance"] is not None

    # the notice is cleared when the new version starts up
    with TestClient(build_app(db=db, background=False)) as restarted:
        assert restarted.get("/api/notice").json()["maintenance"] is None


def test_db_safe_under_concurrent_requests(tmp_path, monkeypatch):
    """API worker threads share one SQLite connection; reads must not collide with writes."""
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv("SNIPER_SECRET_FILE", str(tmp_path / "secret"))
    db = DB(str(tmp_path / "c.db"))
    u = db.create_user("a@example.com", "hunter2hunter2")
    token = db.create_session(u.id)
    w = db.create_watch(Watch(query="RTX 4060", user_id=u.id))

    def work(i):
        for j in range(40):
            assert db.session_user(token).id == u.id
            db.upsert_listing(w.id, f"k{i}-{j}", {}, 100.0, 100.0, "new", 0, "2026-01-01")
            db.get_listing_rows(w.id)
            db.list_watches(u.id)
            db.create_session(u.id)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(work, range(8)))
    assert len(db.get_listing_rows(w.id)) == 320
