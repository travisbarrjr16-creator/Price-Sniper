import base64

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
