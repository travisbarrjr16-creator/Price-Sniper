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


def test_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("SNIPER_DEMO", "1")
    db = DB(str(tmp_path / "api.db"))
    client = TestClient(build_app(db=db, background=False))
    r = client.post("/api/watches", json={"query": "RTX 4060", "max_price": 290, "zip_code": "78701", "radius_miles": 40})
    assert r.status_code == 200
    wid = r.json()["id"]
    items = client.get(f"/api/watches/{wid}/listings").json()
    assert items and all("ti" not in i["title"].lower().split() for i in items)
    assert items == sorted(items, key=lambda i: i["total_cost"])
    assert client.get(f"/api/watches/{wid}/listings?sort=value").status_code == 200
    client.post(f"/api/watches/{wid}/check")
    h = client.get(f"/api/watches/{wid}/history").json()
    assert len(h["snapshots"]) >= 2
    assert client.delete(f"/api/watches/{wid}").json() == {"ok": True}
