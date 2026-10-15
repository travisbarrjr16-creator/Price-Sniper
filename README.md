# 🎯 Price Sniper

A personal deal aggregator. Tell it what you want ("RTX 4060", max $290, used OK,
within 40 miles). It keeps searching every source in the background, works out
what you'd actually pay, filters out junk and scams, and pings your phone when
something good shows up.

## Quick start

```bash
pip install -e .            # or: pip install fastapi uvicorn httpx cryptography
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

## Accounts

Each person signs up with an email and password. Watches, results, alerts,
push settings, and Gmail connection are private to that account. Passwords are
hashed with scrypt, sessions are random tokens stored hashed, and Gmail
refresh tokens are encrypted at rest. Set `SNIPER_INVITE_CODE` so only people
you give the code to can sign up.

Marketplace API keys are server-wide. You set them once in `.env` and every
user's searches use them.

## Sources

| Source | Covers | Free setup |
|---|---|---|
| **eBay** (Browse API) | New, open box, refurb, used, **local pickup within your radius** | [developer.ebay.com](https://developer.ebay.com) → `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` |
| **Best Buy** (Products API) | New + refurb retail | [developer.bestbuy.com](https://developer.bestbuy.com) → `BESTBUY_API_KEY` |
| **Walmart** (Affiliate API) | Walmart.com + marketplace sellers; "Restored" = refurb | [walmart.io](https://walmart.io): make an account, generate an RSA key pair, upload the public key → `WALMART_CONSUMER_ID`, `WALMART_PRIVATE_KEY_PATH`. *Unconfirmed whether new sign-ups are still approved.* |
| **Reverb** | Music gear, some electronics; new/used | Personal token in reverb.com settings → `REVERB_TOKEN` |
| **Etsy** (Open API v3) | Handmade/vintage; shipping is estimated | [etsy.com/developers](https://www.etsy.com/developers) → `ETSY_API_KEY` (+ `ETSY_SHARED_SECRET`) |
| **Email alerts** (Gmail, read-only) | **Craigslist, OfferUp, Mercari, FB Marketplace, Swappa**, through their saved-search alert emails | See below |
| **Demo** | Simulated | On automatically when nothing else is configured |

The **site dropdown** above the results shows one site at a time ("only
Walmart right now"). The **Sites** checkboxes on a new watch choose which
sites that watch searches.

**Not included:** Amazon (its API needs an affiliate account with recent
sales; Keepa is paid), Newegg (no API), Discogs (its API can't search
marketplace listings), and direct Facebook/OfferUp/Mercari search (no public
API; logging in as you would break their terms and risk your account). The
email-alert source covers those sites the legitimate way.

### Gmail alert emails setup (server admin, once)
1. In [Google Cloud Console](https://console.cloud.google.com), create a project, enable the **Gmail API**, and create an **OAuth client ID** (type *Web application*).
2. Add the authorized redirect URI `<PUBLIC_URL>/api/gmail/callback`.
3. On the OAuth consent screen, add the `gmail.readonly` scope. While the app is in **Testing**, add each user's Gmail as a *test user* (up to 100). Publishing it for anyone requires Google's security review, because Gmail is a restricted scope.
4. Put `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.

Then each user goes to **Settings → Connect Gmail** and turns on saved-search
email alerts on Craigslist, OfferUp, and the rest. Matching listings show up
in their results, are scored and filtered like everything else, and trigger
their push alerts. Email layouts vary, so the parsing is best-effort.

## Phone push (ntfy)

Each user installs the **ntfy** app, subscribes to a hard-to-guess topic, and
enters it in **Settings**. Tap a notification to open the listing.

## Running it 24/7 / for other people

The watcher runs only while the server is up. For other people to use it, it
has to run somewhere they can reach, such as a small VPS or a home server
behind a tunnel. Put it behind HTTPS (Caddy, nginx, or Cloudflare Tunnel), set
`PUBLIC_URL=https://your-domain` so cookies are marked secure, and set
`SNIPER_INVITE_CODE`.

```bash
uvicorn sniper.app:app --host 0.0.0.0 --port 8000
```

Data is kept in SQLite (`sniper.db`). Back it up together with
`.sniper_secret`; without the secret, stored Gmail connections can't be decrypted.

## API

`POST /api/auth/signup|login|logout` · `GET/PUT /api/me` · `GET /api/gmail/connect` ·
`GET/POST /api/watches` · `PUT/DELETE /api/watches/{id}` · `POST /api/watches/{id}/check`
· `GET /api/watches/{id}/listings?sort=total|value|distance|newest&site=Walmart&include_hidden=`
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
  security.py   password hashing, sessions, token encryption
  sources/      ebay, bestbuy, walmart, reverb, etsy, gmail (alert emails), demo
```
