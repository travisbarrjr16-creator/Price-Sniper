import asyncio

import httpx

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

    maintenance.announce(db, -(maintenance.STALE_AFTER + 10))   # deadline long gone, but the server never restarted
    assert c.get("/api/notice").json()["maintenance"] is None and db.get_setting("maintenance") is None

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


def test_target_price_and_urgent_alerts(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    src = FakeSource()
    src.items = [L("RTX 4060 a", 300, shipping=0, condition="new"), L("RTX 4060 b", 310, shipping=0, condition="new")]
    w = db.create_watch(Watch(query="RTX 4060", max_price=290, target_price=260))
    sent = []

    class Rec:
        async def send(self, client, title, msg, url, urgent=False, **kw):
            sent.append((msg.splitlines()[0], urgent))

    watcher = Watcher(db, notifier=Rec(), sources=[src])
    asyncio.run(watcher.check(w))
    src.items.append(L("RTX 4060 c", 285, shipping=0, condition="new", source_id="c"))     # new lowest
    asyncio.run(watcher.check(db.get_watch(w.id)))
    src.items.append(L("RTX 4060 d", 255, shipping=0, condition="new", source_id="d"))     # target hit
    asyncio.run(watcher.check(db.get_watch(w.id)))
    assert sent == [("🔥 new lowest price (was $300.00)", True), ("🎯 hit your $260 target", True)]


def test_muted_seller_and_dismissed_listing(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _signup(c)
    wid = c.post("/api/watches", json={"query": "RTX 4060"}).json()["id"]
    items = c.get(f"/api/watches/{wid}/listings").json()["items"]
    seller = next(i for i in items if i["seller_key"])
    n_from_seller = sum(1 for i in items if i["seller_key"] == seller["seller_key"])
    other = next(i for i in items if i["seller_key"] != seller["seller_key"])

    c.post("/api/hidden", json={"kind": "seller", "value": seller["seller_key"], "label": "x"})
    c.post("/api/hidden", json={"kind": "listing", "value": other["key"], "label": "y"})
    res = c.get(f"/api/watches/{wid}/listings").json()
    assert all(i["seller_key"] != seller["seller_key"] and i["key"] != other["key"] for i in res["items"])
    assert res["hidden_by_you"] == n_from_seller + 1
    both = c.get(f"/api/watches/{wid}/listings?include_dismissed=true").json()["items"]
    assert len(both) == len(res["items"]) + n_from_seller + 1

    # "cheaper right now" ignores hidden ones; the cheapest real option says 0
    assert min(i["cheaper_now"] for i in res["items"]) == 0
    c.delete("/api/hidden", params={"kind": "listing", "value": other["key"]})
    assert len(c.get("/api/hidden").json()) == 1

    # someone else's mutes don't affect me
    d = TestClient(c.app); _signup(d, "b@example.com")
    assert d.get("/api/hidden").json() == []


def test_muted_seller_never_alerts(tmp_path):
    db = DB(str(tmp_path / "m.db"))
    u = db.create_user("a@example.com", "hunter2hunter2")
    src = FakeSource()
    src.items = [L("RTX 4060 a", 300, shipping=0, condition="new", seller_name="Spammy")]
    w = db.create_watch(Watch(query="RTX 4060", max_price=290, user_id=u.id))
    watcher = Watcher(db, sources=[src])
    asyncio.run(watcher.check(w))
    db.hide(u.id, "seller", "Test:spammy", "Spammy")
    src.items.append(L("RTX 4060 b", 200, shipping=0, condition="new", seller_name="Spammy", source_id="b"))
    asyncio.run(watcher.check(db.get_watch(w.id)))
    assert db.list_alerts() == []


def test_quiet_hours(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from sniper.models import User
    u = User(id=1, email="x", quiet_start="23:00", quiet_end="07:00", quiet_tz="America/Chicago")
    assert u.in_quiet_hours(datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc))      # midnight in Chicago
    assert not u.in_quiet_hours(datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc))  # noon in Chicago
    # daylight saving: 04:30 UTC is 23:30 in July (quiet) but 22:30 in January (not)
    assert u.in_quiet_hours(datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc))
    assert not u.in_quiet_hours(datetime(2026, 1, 1, 4, 30, tzinfo=timezone.utc))

    c = _client(tmp_path, monkeypatch); _signup(c)
    assert c.put("/api/me/quiet", json={"start": "23:00"}).status_code == 422
    assert c.put("/api/me/quiet", json={"start": "25:00", "end": "07:00"}).status_code == 422
    assert c.put("/api/me/quiet", json={"start": "23:00", "end": "07:00", "tz": "Mars/Base"}).status_code == 422
    me = c.put("/api/me/quiet", json={"start": "23:00", "end": "07:00", "tz": "America/Chicago",
                                      "allow_urgent": False}).json()
    assert (me["quiet_start"], me["quiet_allow_urgent"]) == ("23:00", False)


def test_password_reset_link(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch); _signup(c, "a@example.com")
    db = c.app.state.db
    uid = db.find_user_by_email("a@example.com").id
    token = db.create_reset_token(uid)
    assert c.post("/api/auth/reset", json={"token": "x" * 20, "password": "newpassword1"}).status_code == 400
    assert c.post("/api/auth/reset", json={"token": token, "password": "newpassword1"}).status_code == 200
    assert c.post("/api/auth/reset", json={"token": token, "password": "another-one1"}).status_code == 400  # one use
    other = TestClient(c.app)
    assert other.post("/api/auth/login", json={"email": "a@example.com", "password": "hunter2hunter2"}).status_code == 401
    assert other.post("/api/auth/login", json={"email": "a@example.com", "password": "newpassword1"}).status_code == 200


def test_history_has_typical_range(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch); _signup(c)
    wid = c.post("/api/watches", json={"query": "RTX 4060"}).json()["id"]
    ref = c.get(f"/api/watches/{wid}/history").json()
    used = ref["reference"]["used"]
    assert used["low"] <= used["typical"] <= used["high"]
    assert ref["lowest_seen"] is not None


class LimitedSource(FakeSource):
    """A source with a 300/day limit that really sends one HTTP request per check."""
    key, name, default_daily_limit = "limited", "LimitedSite", 300

    async def search(self, watch, client):
        await client.get("https://limited.example/search")
        return list(self.items)


def test_daily_limit_planning_refuses_and_suggests_interval(tmp_path):
    from sniper import budget
    db = DB(str(tmp_path / "b.db"))
    src = [LimitedSource()]
    # 90% of 300 = 270 planned requests/day. A 15-minute watch needs 96/day.
    for _ in range(2):
        w = Watch(query="RTX 4060", interval_minutes=15)
        assert budget.check_watch(db, w, src) is None
        db.create_watch(w)
    third = Watch(query="RTX 4070", interval_minutes=15)
    msg = budget.check_watch(db, third, src)
    assert msg and "every 20 minutes" in msg          # 192 used, 78 left -> 72/day at 20 min fits
    assert budget.check_watch(db, Watch(query="RTX 4070", interval_minutes=20), src) is None
    assert budget.check_watch(db, Watch(query="x", interval_minutes=15, active=False), src) is None  # paused = free


def test_daily_limit_counts_requests_and_pauses_site(tmp_path, monkeypatch):
    db = DB(str(tmp_path / "r.db"))
    src = LimitedSource()
    src.items = [L("RTX 4060 a", 250, shipping=0, condition="new")]
    hits = []
    transport = httpx.MockTransport(lambda req: hits.append(req.url.host) or httpx.Response(200))
    watcher = Watcher(db, sources=[src], transport=transport)
    w = db.create_watch(Watch(query="RTX 4060"))

    asyncio.run(watcher.check(w))
    assert db.usage_today("limited") == 1 and hits == ["limited.example"]

    db.add_usage("limited", 294)          # 295 of 300 used: next check would pass 98%
    stats = asyncio.run(watcher.check(db.get_watch(w.id)))
    assert hits == ["limited.example"]    # no new request was sent
    assert "paused for today" in stats["errors"][0] and "295 of 300" in stats["errors"][0]
    assert db.get_watch(w.id).last_error.startswith("LimitedSite: paused for today")


def test_limit_override_and_usage_api(tmp_path, monkeypatch):
    s = LimitedSource()
    monkeypatch.setenv("SNIPER_LIMIT_LIMITED", "1000")
    assert s.daily_limit() == 1000
    monkeypatch.setenv("SNIPER_LIMIT_LIMITED", "0")
    assert s.daily_limit() is None                  # 0 = no limit
    monkeypatch.setenv("SNIPER_LIMIT_DEMO", "300")      # plans for 270/day
    c = _client(tmp_path, monkeypatch); _signup(c)
    c.post("/api/watches", json={"query": "RTX 4060", "interval_minutes": 30})
    (row,) = c.get("/api/usage").json()
    assert (row["key"], row["limit"], row["planned_per_day"]) == ("demo", 300, 48)
    # 48 + 288/day (every 5 min) > 270: refused, and told 15 minutes would fit (48 + 96)
    r = c.post("/api/watches", json={"query": "RTX 4070", "interval_minutes": 5})
    assert r.status_code == 429 and "every 15 minutes" in r.json()["detail"]
    assert c.post("/api/watches", json={"query": "RTX 4070", "interval_minutes": 15}).status_code == 200


def test_loop_survives_a_broken_watch(tmp_path):
    """A watch that crashes must not block the others or retry immediately."""
    db = DB(str(tmp_path / "l.db"))
    good_src = FakeSource()
    good_src.items = [L("RTX 4060 a", 250, shipping=0, condition="new")]
    broken = db.create_watch(Watch(query="RTX 4060"))
    good = db.create_watch(Watch(query="RTX 4060"))
    watcher = Watcher(db, sources=[good_src])
    real_check = watcher.check

    async def check(w):
        if w.id == broken.id:
            raise RuntimeError("boom")
        return await real_check(w)
    watcher.check = check

    async def one_pass():
        task = asyncio.create_task(watcher._loop())
        await asyncio.sleep(0.5)
        task.cancel()
    asyncio.run(one_pass())
    assert db.get_watch(good.id).last_checked_at is not None          # the good watch still ran
    b = db.get_watch(broken.id)
    assert b.last_checked_at is not None and "boom" in b.last_error   # error recorded...
    assert not Watcher._due(b)                                       # ...and it waits its interval


def test_several_finds_make_one_phone_notification(tmp_path):
    db = DB(str(tmp_path / "d.db"))
    src = FakeSource()
    src.items = [L("RTX 4060 a", 300, shipping=0, condition="new")]
    w = db.create_watch(Watch(query="RTX 4060", max_price=290, target_price=200))
    pushes = []

    class Rec:
        async def send(self, client, title, msg, url, urgent=False, **kw):
            pushes.append((title, msg, url, urgent))

    watcher = Watcher(db, notifier=Rec(), sources=[src])
    asyncio.run(watcher.check(w))
    src.items += [L("RTX 4060 b", 280, shipping=0, condition="new", source_id="b"),
                  L("RTX 4060 c", 270, shipping=0, condition="new", source_id="c"),
                  L("RTX 4060 d", 190, shipping=0, condition="new", source_id="d")]
    asyncio.run(watcher.check(db.get_watch(w.id)))
    assert len(db.list_alerts()) == 3                 # all three saved for the app
    assert len(pushes) == 1                           # but the phone buzzes once
    title, msg, url, urgent = pushes[0]
    assert title.startswith("3 deals · RTX 4060 · from $190.00")
    assert msg.startswith("🎯 hit your $200 target: $190.00") and url.endswith("RTX 4060 d") and urgent


@pytest.mark.parametrize("query,title,ok", [
    # PC parts: units and kit notation shouldn't matter
    ("DDR5 2x16 6400", "Corsair Vengeance 32GB (2x16GB) DDR5 6400MHz CL32", True),
    ("DDR5 2x16 6400", "G.Skill Trident Z5 RGB DDR5-6400 32GB (2 x 16GB)", True),
    ("ddr5 2x16gb 6400mhz", "Kingston FURY Beast 32GB 2x16GB DDR5 6400 MT/s", True),
    ("DDR5 3600", "G.Skill Trident Z5 DDR4 3600 32GB", False),          # "DDR5" isn't "DDR" + a stray 5
    ("DDR5 2x16 6400", "Corsair 16GB (2x8GB) DDR5 6400 CL16", False),
    ("DDR5 2x16 6400", "Corsair 32GB (2x16GB) DDR5 5600MHz", False),
    ("Ryzen 7 7800X3D", "AMD Ryzen 7 7700X Processor", False),
    ("1TB NVMe SN850X", "WD_BLACK 2TB SN850X NVMe Gen4 SSD", False),
    ("750W 80+ Gold", "Corsair RM750e 750 W 80 Plus Gold", True),
    ("Galaxy S24", "Samsung Galaxy S24+ 256GB", False),                   # S24+ is the Plus model
    ("RTX4060", "MSI GeForce RTX 4060 Ventus 2X", True),
])
def test_part_matching(query, title, ok):
    assert match(L(title), Watch(query=query))[0] is ok


@pytest.mark.parametrize("query,title,ok", [
    ("RTX 4060", "ASUS TUF Gaming Laptop RTX 4060 16GB RAM", False),
    ("RTX 4060", "Gaming PC Ryzen 5 5600 RTX 4060 1TB", False),
    ("RTX 4060", "Lot of 2 RTX 4060 8GB", False),
    ("RTX 4060", "RTX 4060 + Ryzen bundle", False),
    ("RTX 4060", "iBUYPOWER Prebuilt Desktop RTX 4060", False),
    ("RTX 4060", "Gigabyte RTX 4060 Eagle OC 8GB (box included)", True),
    ("RTX 4060 laptop", "ASUS TUF Gaming Laptop RTX 4060 16GB RAM", True),     # asked for a laptop
    ("Ryzen 7 7800X3D", "AMD Ryzen 7 7800X3D + B650 motherboard combo", False),
    ("DDR5 2x16 6400", "Dell XPS desktop i7 DDR5 2x16 6400", False),
    ("Raspberry Pi 5", "Raspberry Pi 5 8GB single board computer", True),       # not a PC-part search
    ("Noctua NH-D15", "Noctua NH-D15 dual tower CPU cooler", True),
    ("iPhone 15", "Apple iPhone 15 128GB bundle with case", True),
])
def test_whole_systems_and_bundles(query, title, ok):
    assert match(L(title), Watch(query=query))[0] is ok
    if not ok:      # turning off "exact model" lets them through
        assert match(L(title), Watch(query=query, strict_variants=False))[0]


def test_relisted_item_is_not_new(tmp_path):
    db = DB(str(tmp_path / "rl.db"))
    src = FakeSource()
    src.items = [L("RTX 4060 x", 280, shipping=0, condition="new", seller_name="Bob", source_id="old")]
    w = db.create_watch(Watch(query="RTX 4060", max_price=290))
    watcher = Watcher(db, sources=[src])
    asyncio.run(watcher.check(w))                                   # first scan: summary alert
    n = len(db.list_alerts())
    src.items = [L("RTX 4060 x", 279, shipping=0, condition="new", seller_name="Bob", source_id="relisted")]
    asyncio.run(watcher.check(db.get_watch(w.id)))
    assert len(db.list_alerts()) == n                               # same seller/title/price: no "new listing" alert
    src.items = [L("RTX 4060 x", 220, shipping=0, condition="new", seller_name="Bob", source_id="relisted2")]
    asyncio.run(watcher.check(db.get_watch(w.id)))
    assert len(db.list_alerts()) == n + 1                           # but a real price cut still alerts


def test_slow_site_is_skipped_not_waited_on(tmp_path, monkeypatch):
    import sniper.watcher as wmod
    monkeypatch.setattr(wmod, "SITE_TIME_LIMIT", 0.2)

    class Slow(FakeSource):
        key, name = "slow", "SlowSite"

        async def search(self, watch, client):
            await asyncio.sleep(5)
            return []
    db = DB(str(tmp_path / "s.db"))
    fast = FakeSource(); fast.items = [L("RTX 4060 a", 250, shipping=0, condition="new")]
    w = db.create_watch(Watch(query="RTX 4060"))
    stats = asyncio.run(Watcher(db, sources=[Slow(), fast]).check(w))
    assert stats["matched"] == 1 and "SlowSite: took longer than" in stats["errors"][0]


def test_walmart_skips_out_of_stock():
    from sniper.sources.walmart import parse_items
    items = parse_items([{"itemId": 1, "name": "RTX 4060", "salePrice": 280, "availableOnline": False},
                         {"itemId": 2, "name": "RTX 4060", "salePrice": 290, "stock": "Not available"},
                         {"itemId": 3, "name": "RTX 4060", "salePrice": 300, "availableOnline": True}])
    assert [i.source_id for i in items] == ["3"]


def test_purge_and_backup(tmp_path, monkeypatch):
    import tarfile
    from datetime import timedelta
    from sniper import housekeeping
    from sniper.models import utcnow
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("EBAY_CLIENT_ID=x\n")
    db = DB(str(tmp_path / "h.db"))
    w = db.create_watch(Watch(query="RTX 4060"))
    old = (utcnow() - timedelta(days=90)).isoformat()
    db.upsert_listing(w.id, "gone-old", {}, 1, 1, "new", 0, old)
    db.upsert_listing(w.id, "active-old", {}, 1, 1, "new", 0, old)
    db.mark_inactive_except(w.id, {"active-old"})
    db.add_snapshot(w.id, (utcnow() - timedelta(days=400)).isoformat(), "new", 1, 1, 1)
    db.add_snapshot(w.id, utcnow().isoformat(), "new", 1, 1, 1)
    counts = housekeeping.purge(db)
    assert counts["listings"] == 1 and counts["snapshots"] == 1
    assert [r["key"] for r in db.get_listing_rows(w.id, active_only=False)] == ["active-old"]   # still-listed items stay

    for _ in range(3):
        archive = housekeeping.backup(db, tmp_path / "backups", keep=2)
    assert len(list((tmp_path / "backups").glob("*.tar.gz"))) == 2                 # old backups rotated out
    with tarfile.open(archive) as tar:
        assert {"sniper.db", ".env"} <= set(tar.getnames())
    assert oct(archive.stat().st_mode)[-3:] == "600"


def test_settings_extras_privacy_delete_and_test_alert(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch); _signup(c, "a@example.com")
    assert c.put("/api/me", json={"ntfy_topic": "short-topic"}).status_code == 422          # 11 chars: guessable
    assert c.put("/api/me", json={"ntfy_topic": "sniper-k3x9v2q7m4"}).status_code == 200
    sent = []

    async def fake_send(client, title, msg, url, **kw):
        sent.append((title, kw.get("ntfy_topic")))
        return {"ntfy": None}
    c.app.state.watcher.notifier.send = fake_send
    r = c.post("/api/me/test-alert")
    assert r.status_code == 200 and r.json()["sent_to"] == ["ntfy"]
    assert sent == [("🎯 Price Sniper test", "sniper-k3x9v2q7m4")]
    assert c.post("/api/me/test-alert").status_code == 429                                  # no spamming
    assert "Price Sniper privacy" in c.get("/privacy").text
    c.post("/api/watches", json={"query": "RTX 4060"})
    assert c.post("/api/me/delete", json={"password": "wrong"}).status_code == 401
    assert c.post("/api/me/delete", json={"password": "hunter2hunter2"}).status_code == 200
    assert c.get("/api/me").status_code == 401
    db = c.app.state.db
    assert db.find_user_by_email("a@example.com") is None and db.list_watches() == []
    _signup(c, "a@example.com")                                                                # email is free again


def test_only_one_process_runs_the_watches(tmp_path):
    import subprocess, sys, textwrap
    db_path = str(tmp_path / "one.db")
    DB(db_path)
    holder = textwrap.dedent(f"""
        import asyncio, time
        from sniper.db import DB
        from sniper.watcher import Watcher
        async def main():
            w = Watcher(DB({db_path!r})); print(w.start(), flush=True); await asyncio.sleep(30)
        asyncio.run(main())
    """)
    proc = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True,
                            env={**__import__("os").environ, "SNIPER_HOUSEKEEPING": "0"})
    try:
        assert proc.stdout.readline().strip() == "True"          # first process gets to run the watches

        async def second():
            w = Watcher(DB(db_path))
            started = w.start()
            await w.stop()
            return started
        assert asyncio.run(second()) is False                     # a second copy only serves the page
    finally:
        proc.kill()


def test_failed_push_is_reported(tmp_path, monkeypatch):
    import httpx
    from sniper.alerts import Notifier
    c = _client(tmp_path, monkeypatch); _signup(c)
    c.put("/api/me", json={"ntfy_topic": "sniper-k3x9v2q7m4"})
    n = Notifier()

    async def send(client, *a, **kw):        # real Notifier logic against a fake ntfy that says "429"
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as fake:
            return await Notifier.send(n, fake, *a, **kw)
    c.app.state.watcher.notifier.send = send
    r = c.post("/api/me/test-alert")
    assert r.status_code == 502 and "rate limited" in r.json()["detail"]
    assert "rate limited" in c.get("/api/me").json()["push_error"]["errors"]["ntfy"]


def test_security_headers_and_cross_site_block(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer"
    evil = c.post("/api/auth/signup", json={"email": "x@example.com", "password": "supersecret1"},
                  headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    ok = c.post("/api/auth/signup", json={"email": "x@example.com", "password": "supersecret1"},
                headers={"Origin": "http://testserver"})
    assert ok.status_code == 200
    # behind a proxy (e.g. Tailscale) Host may be rewritten; PUBLIC_URL or X-Forwarded-Host still match
    monkeypatch.setenv("PUBLIC_URL", "https://my-pc.tail1234.ts.net")
    via_proxy = c.post("/api/auth/login", json={"email": "x@example.com", "password": "supersecret1"},
                       headers={"Origin": "https://my-pc.tail1234.ts.net"})
    assert via_proxy.status_code == 200
    fwd = c.post("/api/auth/login", json={"email": "x@example.com", "password": "supersecret1"},
                 headers={"Origin": "https://other.ts.net", "X-Forwarded-Host": "other.ts.net"})
    assert fwd.status_code == 200
    bad = c.post("/api/auth/login", json={"email": "x@example.com", "password": "supersecret1"},
                 headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403 and "PUBLIC_URL" in bad.json()["detail"]


def test_non_web_links_are_dropped(tmp_path):
    db = DB(str(tmp_path / "u.db"))
    src = FakeSource()
    src.items = [L("RTX 4060 a", 250, shipping=0, condition="new"),
                 Listing(source="Test", source_id="evil", title="RTX 4060 evil", url="javascript:alert(1)", price=200),
                 L("RTX 4060 no price", 0, shipping=0, condition="new")]       # missing price -> $0
    w = db.create_watch(Watch(query="RTX 4060"))
    stats = asyncio.run(Watcher(db, sources=[src]).check(w))
    assert stats["fetched"] == 1 and all(r["key"] != "Test:evil" for r in db.get_listing_rows(w.id))


def test_editing_target_rechecks_known_listings(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch); _signup(c)
    db, watcher = c.app.state.db, c.app.state.watcher
    src = FakeSource(); src.items = [L("RTX 4060 a", 240, shipping=0, condition="new")]
    watcher._sources = [src]
    w = c.post("/api/watches", json={"query": "RTX 4060", "max_price": 200}).json()   # $240 is over the max
    asyncio.run(watcher.check(db.get_watch(w["id"])))
    assert db.list_alerts() == []
    c.put(f"/api/watches/{w['id']}", json={"query": "RTX 4060", "max_price": 300, "target_price": 250})
    asyncio.run(watcher.check(db.get_watch(w["id"])))
    (a,) = db.list_alerts()
    assert "250 target" in a["reason"]                         # the known $240 listing now alerts once
    asyncio.run(watcher.check(db.get_watch(w["id"])))
    assert len(db.list_alerts()) == 1                          # and only once


def test_sliding_session_and_watchlist_ignores_hidden(tmp_path, monkeypatch):
    from datetime import timedelta
    from sniper.models import utcnow
    c = _client(tmp_path, monkeypatch); _signup(c)
    db = c.app.state.db
    soon = (utcnow() + timedelta(days=2)).isoformat()
    db.conn.execute("UPDATE sessions SET expires_at=?", (soon,)); db.conn.commit()
    assert c.get("/api/me").status_code == 200
    assert db.conn.execute("SELECT expires_at FROM sessions").fetchone()[0] > (utcnow() + timedelta(days=29)).isoformat()

    w = c.post("/api/watches", json={"query": "RTX 4060"}).json()
    items = c.get(f"/api/watches/{w['id']}/listings").json()["items"]
    cheapest = min(items, key=lambda i: i["total_cost"])
    c.post("/api/hidden", json={"kind": "listing", "value": cheapest["key"]})
    (row,) = c.get("/api/watches").json()
    assert row["lowest"] >= cheapest["total_cost"] and row["count"] == len(items) - 1      # hidden one not counted


def test_push_payload_limits():
    import asyncio
    import json as _json

    import httpx

    from sniper.alerts import Notifier, clip

    sent = []
    async def go():
        transport = httpx.MockTransport(lambda req: (sent.append(_json.loads(req.content)), httpx.Response(200))[1])
        async with httpx.AsyncClient(transport=transport) as client:
            return await Notifier().send(client, "3 deals", "• $230 @everyone GPU 🎮\n" * 400, "https://ebay.com/itm/1",
                                         ntfy_topic="abcdefghijkl",
                                         discord_webhook="https://discord.com/api/webhooks/1/abc")
    assert asyncio.run(go()) == {"ntfy": None, "Discord": None}
    ntfy, discord = sent
    assert len(ntfy["message"].encode()) <= 3800 and ntfy["message"].endswith("…")
    assert len(discord["content"]) <= 2000 and discord["content"].endswith("https://ebay.com/itm/1")
    assert discord["allowed_mentions"] == {"parse": []}
    assert clip("é" * 10, 7) == "éé…"          # never splits a character
