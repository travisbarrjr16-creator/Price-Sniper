import base64

import pytest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from sniper.sources.etsy import parse_results
from sniper.sources.gmail import parse_alert_html
from sniper.sources.reverb import parse_listings
from sniper.sources.walmart import parse_items, sign_headers


def test_walmart_signature_verifies():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    h = sign_headers("consumer-123", "1", pem, ts_ms=1700000000000)
    assert h["WM_CONSUMER.INTIMESTAMP"] == "1700000000000"
    key.public_key().verify(base64.b64decode(h["WM_SEC.AUTH_SIGNATURE"]),
                            b"consumer-123\n1700000000000\n1\n", padding.PKCS1v15(), hashes.SHA256())


def test_walmart_parse():
    items = parse_items([
        {"itemId": 1, "name": "MSI RTX 4060 Ventus", "salePrice": 289.0, "freeShippingOver35Dollars": True,
         "sellerInfo": "Walmart.com", "productTrackingUrl": "https://goto.walmart.com/x"},
        {"itemId": 2, "name": "Restored RTX 4060", "salePrice": 240.0, "standardShipRate": 5.99, "sellerInfo": "GPU Guys"},
        {"itemId": 3, "name": "no price"},
    ])
    assert [i.condition for i in items] == ["new", "refurbished"]
    assert items[0].shipping == 0.0 and items[0].seller_rating == 100.0
    assert items[1].shipping == 5.99 and items[1].seller_rating is None


def test_reverb_parse():
    data = {"listings": [{
        "id": 77, "title": "RTX 4060 8GB", "price": {"amount": "250.00"},
        "condition": {"display_name": "Excellent"}, "shop_name": "gearhead",
        "shipping": {"rates": [{"region_code": "US_CON", "rate": {"amount": "15.00"}}]},
        "_links": {"web": {"href": "https://reverb.com/item/77"}}, "photos": [],
    }]}
    (l,) = parse_listings(data)
    assert (l.price, l.shipping, l.condition, l.url) == (250.0, 15.0, "used", "https://reverb.com/item/77")


def test_etsy_parse():
    (l,) = parse_results({"results": [{"listing_id": 5, "title": "Vintage RTX 4060 case mod",
                                       "price": {"amount": 12999, "divisor": 100}, "url": "https://etsy.com/l/5"}]})
    assert l.price == 129.99 and l.condition == "used" and l.shipping is None


def test_gmail_alert_parse_merges_links_and_unwraps_redirects():
    html = """
    <a href="https://austin.craigslist.org/sop/d/austin-msi-rtx-4060/7801234567.html"><img src=x></a>
    <a href="https://austin.craigslist.org/sop/d/austin-msi-rtx-4060/7801234567.html">MSI RTX 4060 Ventus 8GB</a> &mdash; $240
    <a href="https://click.offerup.com/r?u=https%3A%2F%2Fofferup.com%2Fitem%2Fdetail%2Fabc-123">$1,255</a> ASUS Dual RTX 4060
    <a href="https://example.com/unsubscribe">Unsubscribe</a>
    """
    got = {l.source: l for l in parse_alert_html(html)}
    assert set(got) == {"Craigslist", "OfferUp"}
    assert got["Craigslist"].title == "MSI RTX 4060 Ventus 8GB" and got["Craigslist"].price == 240.0
    assert got["OfferUp"].price == 1255.0 and got["OfferUp"].url == "https://offerup.com/item/detail/abc-123"
    assert got["Craigslist"].local_pickup and not got["Craigslist"].taxable


from sniper.sources.reddit import parse_bapcs, parse_hws


def test_reddit_buildapcsales():
    l = parse_bapcs({"id": "a", "title": "[GPU] MSI RTX 4060 Ventus 2X 8GB ($299.99 - $20 MIR = $279.99)",
                     "url": "https://www.newegg.com/p/123", "domain": "newegg.com", "permalink": "/r/x"})
    assert (l.price, l.title, l.url, l.condition) == (279.99, "MSI RTX 4060 Ventus 2X 8GB", "https://www.newegg.com/p/123", "new")
    assert parse_bapcs({"id": "b", "title": "[GPU] RTX 4060 $250", "link_flair_text": "Expired"}) is None


def test_reddit_hardwareswap():
    post = {"id": "b", "title": "[USA-TX] [H] MSI RTX 4060 Ventus, Ryzen 5 5600 [W] PayPal, Local Cash",
            "selftext": "Timestamps: imgur\n\nMSI RTX 4060 Ventus - $230 shipped\nRyzen 5 5600 - $90",
            "permalink": "/r/hardwareswap/b", "author": "bob", "author_flair_text": "Trades: 12", "created_utc": 1}
    l = parse_hws(post, "RTX 4060")
    assert (l.price, l.shipping, l.seller_feedback_count, l.location) == (230.0, 0.0, 12, "USA-TX")
    assert parse_hws({**post, "title": "[USA-TX] [H] PayPal [W] RTX 4060"}, "RTX 4060") is None     # buying post
    assert parse_hws({**post, "title": "[USA-TX] [H] RTX 4060 [W] RTX 3080"}, "RTX 4060") is None   # trade only


def test_walmart_keys_command(tmp_path, monkeypatch):
    import pytest
    from sniper import admin
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("WALMART_CONSUMER_ID=\nWALMART_PRIVATE_KEY_PATH=\n")
    public = admin.walmart_keys()
    assert public.startswith("-----BEGIN PUBLIC KEY-----")
    assert "WALMART_PRIVATE_KEY_PATH=keys/walmart_private.pem" in (tmp_path / ".env").read_text()
    assert oct((tmp_path / "keys/walmart_private.pem").stat().st_mode)[-3:] == "600"
    # the generated private key signs requests that verify with the public key we'd upload
    pem = (tmp_path / "keys/walmart_private.pem").read_bytes()
    h = sign_headers("cid", "1", pem, ts_ms=1)
    pub = serialization.load_pem_public_key(public.encode())
    pub.verify(base64.b64decode(h["WM_SEC.AUTH_SIGNATURE"]), b"cid\n1\n1\n", padding.PKCS1v15(), hashes.SHA256())
    with pytest.raises(SystemExit):
        admin.walmart_keys()              # never silently replaces a key already uploaded to Walmart


def test_set_env_values_adds_and_replaces(tmp_path):
    from sniper.admin import set_env_values
    env = tmp_path / ".env"
    env.write_text("A=1\nB=2")
    set_env_values({"B": "3", "C": "4"}, env)
    assert env.read_text() == "A=1\nB=3\nC=4\n"


@pytest.mark.parametrize("body,price", [
    ("MSI RTX 4060 Ventus - ~~$260~~ $230 shipped", 230.0),          # price drop: use the new price
    ("MSI RTX 4060 Ventus - ~~$260~~ ~~$245~~ $230", 230.0),
    ("~~MSI RTX 4060 Ventus - $230~~", None),                         # crossed out = sold
    ("MSI RTX 4060 Ventus - $230 SOLD", None),
    ("MSI RTX 4060 Ventus - $230 (pending)", None),
    ("~~Ryzen 5 5600 - $90~~ SOLD\nMSI RTX 4060 Ventus - $230", 230.0),
    ("MSI RTX 4060 Ventus - $1.2k or best offer", 1200.0),
    ("MSI RTX 4060 Ventus - $1,050.00", 1050.0),
])
def test_hardwareswap_current_prices_only(body, price):
    post = {"id": "z", "title": "[USA-TX][H] MSI RTX 4060 Ventus [W] PayPal", "selftext": body,
            "permalink": "/x", "author": "a", "created_utc": 1}
    got = parse_hws(post, "RTX 4060")
    assert (got.price if got else None) == price


def test_ebay_listing_parse_and_headers():
    import asyncio
    import httpx
    from sniper.models import Watch
    from sniper.sources.ebay import EbaySource
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": f"tok{len(seen)}", "expires_in": 7200})
        if req.headers["authorization"] == "Bearer tok1":
            return httpx.Response(401)                               # first token rejected early
        return httpx.Response(200, json={"itemSummaries": [{
            "itemId": "v1|123|0", "title": "MSI RTX 4060 Ventus 2X 8GB", "price": {"value": "249.99", "currency": "USD"},
            "conditionId": "3000", "itemWebUrl": "https://www.ebay.com/itm/123",
            "shippingOptions": [{"shippingCost": {"value": "12.50"}}, {"shippingCost": {"value": "0.00"}}],
            "seller": {"username": "gpuguy", "feedbackPercentage": "99.6", "feedbackScore": 812},
            "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"], "itemGroupType": "SELLER_DEFINED_VARIATIONS"}]})

    src = EbaySource()
    src.client_id, src.client_secret = "id", "secret"
    w = Watch(query="RTX 4060", zip_code="78701")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await src.search(w, c)
    (l,) = asyncio.run(run())
    assert (l.price, l.shipping, l.condition, l.seller_rating, l.seller_feedback_count) == (249.99, 0.0, "used", 99.6, 812)
    assert l.price_varies
    searches = [r for r in seen if "item_summary" in r.url.path]
    assert len(searches) == 2                                         # retried once with a fresh token
    assert searches[-1].headers["x-ebay-c-enduserctx"] == "contextualLocation=country%3DUS%2Czip%3D78701"
    f = searches[-1].url.params["filter"]
    assert "priceCurrency:USD" in f and "buyingOptions:{FIXED_PRICE|BEST_OFFER}" in f and "conditionIds:{" in f


def test_gmail_expired_connection_asks_to_reconnect(tmp_path, monkeypatch):
    import asyncio
    import httpx
    from sniper.db import DB
    from sniper.models import Watch
    from sniper.sources.gmail import GmailAlertsSource
    monkeypatch.setenv("SNIPER_SECRET_FILE", str(tmp_path / "s"))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id"); monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "sec")
    db = DB(str(tmp_path / "g.db"))
    u = db.create_user("a@example.com", "hunter2hunter2")
    db.set_gmail(u.id, "deals@gmail.com", "refresh-1")
    src = GmailAlertsSource(); src.db = db
    transport = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))

    async def run():
        async with httpx.AsyncClient(transport=transport) as c:
            return await src.search(Watch(query="RTX 4060", user_id=u.id), c)
    with pytest.raises(RuntimeError, match="Reconnect"):
        asyncio.run(run())
    assert not db.gmail_connected(u.id) and db.get_user(u.id).gmail_email == "deals@gmail.com"
    assert asyncio.run(run()) == []         # afterwards it quietly skips instead of erroring every check
