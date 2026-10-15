# 🎯 Price Sniper

A personal deal aggregator. Tell it what you want ("RTX 4060", max $290, used OK,
within 40 miles). It keeps searching every source in the background, works out
what you'd actually pay, filters out junk and scams, and pings your phone when
something good shows up.

## Quick start

```bash
pip install -e .            # or: pip install fastapi uvicorn httpx
cp .env.example .env        # add keys (optional; see below)
uvicorn sniper.app:app --port 8000
# open http://localhost:8000
```

With no keys set it runs on the **Demo** source, a simulated marketplace that
changes over time (listings sell, prices drop, scams show up), so you can try
the whole flow right away.

## What it does

| Feature | How |
|---|---|
| **Search once, watch continuously** | Each watch runs every N minutes in a background loop. The first check runs as soon as you create the watch. |
| **Unified results** | Every source is mapped to one `Listing` shape. **Total = price + shipping (estimated at $15 if unknown, $0 for local pickup) + sales tax** (not added for private local sales). Shows distance, condition, and seller rating. |
| **Sort** | *Cheapest* (total), *Best value* (total adjusted for condition, risk, driving cost, and unknown shipping), *Nearest*, *Newest*. |
| **Smart matching** | All query words must be in the title. **Exact-model mode** rejects variants: "4060" won't match "4060 Ti/Super". It also handles `RTX4060`/`8 GB` spellings, must-include terms (`8gb`), exclude terms (`laptop`), and drops junk titles (empty box, for parts, WTB, backplate…). |
| **Scam filter** | A risk score from price far below typical, a brand-new or low-feedback seller, a rating under 95%, and payment red flags ("zelle only", "shipping only", gift cards…). Risky listings are hidden by default and labeled "Too good to be true". |
| **Is this a deal?** | Each listing is compared to the median total of every non-risky listing seen in the last 30 days, with new and used tracked separately. Labels: Great (≤ −15%), Good, Typical, Above typical. A chart shows the lowest price over time. |
| **Alerts** | Sent when a listing is **new or cut in price** and either **beats the current lowest** or is **under your max**. The first scan sends one summary alert, not a flood. Delivered through ntfy (phone push), Discord, browser notifications, and in-app toasts. |
| **One-tap open** | Every result and every push notification links straight to the listing. On a phone, eBay/Best Buy links open in their apps. |

## Sources

| Source | Covers | Setup |
|---|---|---|
| **eBay** (Browse API) | new, open box, refurb, used, **local pickup within your radius**, shipping to your ZIP | Free keys at [developer.ebay.com](https://developer.ebay.com) → `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (production keyset) |
| **Best Buy** (Products API) | new and refurb retail | Key at [developer.bestbuy.com](https://developer.bestbuy.com) → `BESTBUY_API_KEY` |
| **Demo** | simulated | On automatically when nothing else is configured. `SNIPER_DEMO=1` forces it on, `0` forces it off. |

**Facebook Marketplace, OfferUp, Craigslist, Swappa:** none of these has a
public search API, and scraping them breaks their terms (and breaks often).
The adapter interface is small, so if you get legitimate access (a partner API
or an alert-email feed), write a class in `sniper/sources/` that implements
`configured()` and `search()` and returns `Listing`s, then add it to
`ALL_SOURCES`. Matching, scoring, history, and alerts all work for it with no
other changes.

## Phone push (ntfy)

1. Install the **ntfy** app (iOS/Android) and subscribe to a hard-to-guess topic, e.g. `sniper-7f3k9q`.
2. Set `NTFY_TOPIC=sniper-7f3k9q` in `.env`.
3. Alerts show price, reason, and how far below typical it is. Tap one to open the listing.

## Running it 24/7

The watcher runs only while the server is up. Leave it on a home PC, a
Raspberry Pi, or a small VPS (`uvicorn sniper.app:app --host 0.0.0.0`). Data
is kept in SQLite (`sniper.db`, or set `SNIPER_DB`).

## API

`GET/POST /api/watches` · `PUT/DELETE /api/watches/{id}` · `POST /api/watches/{id}/check`
· `GET /api/watches/{id}/listings?sort=total|value|distance|newest&include_hidden=`
· `GET /api/watches/{id}/history` · `GET /api/alerts?since_id=` · `GET /api/sources`

## Tests

```bash
pip install pytest && pytest -q
```

## Layout

```
sniper/
  models.py     Listing / Watch types
  matching.py   strict title matching, variants, junk
  scoring.py    total cost, risk score, best value, deal rating
  watcher.py    background polling, history, alert decisions
  alerts.py     ntfy + Discord
  db.py         SQLite
  app.py        FastAPI + serves static/index.html
  sources/      ebay.py, bestbuy.py, demo.py (add yours here)
```
