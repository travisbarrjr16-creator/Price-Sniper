import asyncio
import json
import time

import httpx

from sniper.browse import Browser, filter_items, guess_category
from sniper.db import DB
from sniper.sources.reddit import RedditSource


def _post(pid, title, body="", flair=None, age=60, url=None, permalink=None):
    return {"id": pid, "title": title, "selftext": body, "link_flair_text": flair,
            "created_utc": time.time() - age, "url": url or f"https://www.reddit.com/r/x/{pid}",
            "permalink": permalink or f"/r/x/comments/{pid}", "domain": "newegg.com", "author": "someone"}


FAKE = {
    "buildapcsales": [_post("a1", "[GPU] MSI RTX 4060 Ventus 2X 8GB ($299.99 - $20 = $279.99)", url="https://newegg.com/p/1"),
                      _post("a2", "[RAM] G.Skill 32GB DDR5 6000 $89.99", url="https://newegg.com/p/2", flair="Expired"),
                      _post("a3", "[SSD] WD SN850X 2TB $119.99", url="https://amazon.com/p/3", age=10 * 86400)],
    "hardwareswap": [_post("h1", "[USA-TX] [H] RTX 3080 FE [W] PayPal, Local", "RTX 3080 FE - ~~$400~~ $360 shipped"),
                     _post("h2", "[USA-CA] [H] PayPal [W] RTX 4090", "buying"),
                     _post("h3", "[USA-NY] [H] Ryzen 7 5800X3D [W] PayPal", "$250", flair="CLOSED")],
    "homelabsales": [_post("l1", "[FS] [US-CA] Dell R730xd 12LFF", "Asking $300 shipped")],
    "appleswap": [_post("p1", "[USA-FL] [H] iPad Air 5 64GB [W] PayPal", "$350")],
    "gamesale": [_post("g1", "[USA-OH] [H] PS5 Slim Disc [W] PayPal", "$380 shipped")],
}


def _transport():
    def handler(req: httpx.Request):
        if "access_token" in str(req.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        sub = req.url.path.split("/")[2]
        return httpx.Response(200, json={"data": {"children": [{"data": p} for p in FAKE[sub]]}})
    return httpx.MockTransport(handler)


def test_browse_feed_parses_and_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    db = DB(str(tmp_path / "b.db"))
    b = Browser(db, RedditSource(), transport=_transport())
    items, _, note = asyncio.run(b.items())
    by_id = {i["id"]: i for i in items}
    assert note is None
    assert set(by_id) == {"buildapcsales:a1", "hardwareswap:h1", "homelabsales:l1", "appleswap:p1", "gamesale:g1"}
    gpu = by_id["buildapcsales:a1"]
    assert (gpu["price"], gpu["category"], gpu["url"]) == (279.99, "GPU", "https://newegg.com/p/1")
    assert by_id["hardwareswap:h1"]["price"] == 360 and by_id["hardwareswap:h1"]["condition"] == "used"
    assert by_id["homelabsales:l1"]["category"] == "Networking & servers"
    assert by_id["appleswap:p1"]["category"] == "Apple" and by_id["gamesale:g1"]["category"] == "Games & consoles"
    assert db.usage_today("reddit") == 6                     # 1 sign-in + 5 subreddits
    asyncio.run(b.items())
    assert db.usage_today("reddit") == 6                     # cached: no more calls
    assert {i["id"] for i in filter_items(items, category="GPU")} == {"buildapcsales:a1", "hardwareswap:h1"}
    assert [i["id"] for i in filter_items(items, q="rtx 3080")] == ["hardwareswap:h1"]
    assert all(i["price"] <= 300 for i in filter_items(items, max_price=300))


def test_browse_demo_without_reddit_and_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sniper.app import build_app
    db = DB(str(tmp_path / "a.db"))
    c = TestClient(build_app(db=db, background=False))
    assert c.get("/api/browse").status_code == 401
    c.post("/api/auth/signup", json={"email": "b@example.com", "password": "supersecret1"})
    r = c.get("/api/browse").json()
    assert r["demo"] and r["items"] and "GPU" in r["categories"]
    gpu = c.get("/api/browse", params={"category": "GPU"}).json()
    assert gpu["items"] and all(i["category"] == "GPU" for i in gpu["items"])
    cheap = c.get("/api/browse", params={"sort": "price"}).json()["items"]
    assert [i["price"] for i in cheap] == sorted(i["price"] for i in cheap)


def test_guess_category():
    assert guess_category("[Monitor] LG 27GP850") == "Monitor"
    assert guess_category("Corsair 32GB DDR5 6000") == "RAM"
    assert guess_category("Logitech G Pro X Superlight mouse") == "Peripherals"
    assert guess_category("random thing", "gamesale") == "Games & consoles"
