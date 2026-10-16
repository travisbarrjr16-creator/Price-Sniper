# 🎯 Price Sniper

A personal deal aggregator. Tell it what you want ("RTX 4060", max $290, used OK,
within 40 miles). It keeps searching every source in the background, works out
what you'd actually pay, filters out junk and scams, and pings your phone when
something good shows up.

> **Running it for yourself and a few friends?**
> - On a computer at home that stays on: **[HOME_SETUP.md](HOME_SETUP.md)** (one setup script for Windows/Mac/Linux)
> - On Google Cloud's free server: **[CLOUD_SETUP.md](CLOUD_SETUP.md)** (watch out for the disk-type trap and the possible IP fee)

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
| **Browse deals** | A 🛍 tab with fresh deals and for-sale posts from r/buildapcsales, r/hardwareswap, r/homelabsales, r/appleswap and r/gamesale, with no watch needed. Filter by category (GPU, CPU, RAM…), search, max price, or site. **＋ Watch this** turns any deal into a watch. One shared fetch every 10 minutes (6 Reddit calls), so it costs the same for 1 user or 6. Shows sample deals until Reddit keys are added. |
| **One-tap open** | Every result and every push notification links straight to the listing. On a phone, eBay/Best Buy links open in their apps. |

## Day-to-day tools

- **Badges:** 🏆 *Lowest ever* (the lowest total this watch has seen), **NEW** (found since your last visit; watches with new finds get a NEW tag too), "found 12 min ago · was $X".
- **Compact view** and an instant **"Search these results"** box above the listings.
- **Edit** a watch in place (its price history is kept). Changing the max or target re-checks known listings once.
- **🔔 Recent alerts** in the top bar, with an unread count, so you can see alerts you missed.
- **Chart:** dashed lines for your max and 🎯 target.
- **Install it like an app:** on a phone, open the site and use *Share → Add to Home Screen* (iPhone) or *Install app* (Android).
- **Settings:** *Generate* a safe ntfy topic, **🔔 Send test notification**, failed deliveries are shown, *Delete my account*. There's also a [privacy page](sniper/static/privacy.html) at `/privacy`.
- **Smarter matching for PC parts:** laptops, prebuilt PCs, lots and bundles don't count as the part (with *Exact model* on); "DDR5 2x16 6400" matches "32GB (2x16GB) DDR5-6400MHz".

- **🎯 Target price:** an optional price you'd jump at, separate from the max. When a listing hits it, you get an urgent alert and the listing gets a 🎯 badge.
- **Alert types:** 🎯 target hit and 🔥 new lowest are sent as urgent. ⬇ price drop under max and 🆕 new under max are normal priority, unless the price rates as a great deal.
- **✕ Not interested / 🔇 Mute seller** on every listing. Hidden items stop alerting. You can undo from the pop-up, from "Show N you hid", or from Settings.
- **Deal context:** typical range (middle half of prices, 30 days), how many options are cheaper right now, and the lowest you've ever seen for that watch.
- **Duplicate** copies a watch's settings into the form, so watching the next model up takes a few seconds.
- **Quiet hours** (Settings): no phone push during the set hours. You can let urgent alerts through. Alerts are still saved in the app.
- **Password reset without email:** the admin runs `python -m sniper.admin reset-link person@example.com` and sends the person the one-time link (valid 24h). Using it signs them out everywhere else. `python -m sniper.admin users` lists accounts.

## Shared daily limits

Everyone's searches use the same marketplace keys, and each site caps requests per day (eBay's default is about 5,000). The app keeps everyone inside those caps:
- **Planning:** a new or resumed watch is refused if all active watches together would need more than 90% of a site's daily limit. The message suggests a check interval that would fit ("Set this watch to check every 30 minutes or more…").
- **Running:** every real request is counted. If a site gets near its limit, it's skipped until midnight UTC with a clear message on the watch, instead of failed searches.
- **Settings → Shared daily search limits** shows each site's usage today and what all watches need per day.

Rule of thumb for eBay: one watch checking every 15 minutes with local pickup uses about 190 requests a day, so about 25 such watches across all users. Set each site's real quota with `SNIPER_LIMIT_<SITE>` in `.env`.

## Accounts

Each person signs up with an email and password. Watches, results, alerts,
push settings, and Gmail connection are private to that account. Passwords are
hashed with scrypt, sessions are random tokens stored hashed, and Gmail
refresh tokens are encrypted at rest. Set `SNIPER_INVITE_CODE` so only people
you give the code to can sign up.

Marketplace API keys are server-wide. You set them once in `.env` and every
user's searches use them.

## Sources

**Step-by-step key setup: [SITE_KEYS.md](SITE_KEYS.md).** Check them with `python -m sniper.admin test-sources`.

| Source | Covers | Free setup |
|---|---|---|
| **eBay** (Browse API) | New, open box, refurb, used, **local pickup within your radius** | [developer.ebay.com](https://developer.ebay.com) → `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` |
| **Best Buy** (Products API) | New + refurb retail | [developer.bestbuy.com](https://developer.bestbuy.com) → `BESTBUY_API_KEY` |
| **Walmart** (Affiliate API) | Walmart.com + marketplace sellers; "Restored" = refurb | [walmart.io](https://walmart.io) account; `python -m sniper.admin walmart-keys` makes the key pair to upload → `WALMART_CONSUMER_ID`. Steps in [SITE_KEYS.md](SITE_KEYS.md). *Unconfirmed whether new sign-ups are still approved.* |
| **Reddit** (official API) | **r/buildapcsales** retailer deals (price from the title) and **r/hardwareswap** used parts. For r/hardwareswap, only selling posts are used, the price comes from the post body, and the seller's "Trades: N" counts as feedback | [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps), create a *script* app → `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` |
| **Reverb** | Music gear, some electronics; new/used | Personal token in reverb.com settings → `REVERB_TOKEN` |
| **Etsy** (Open API v3) | Handmade/vintage; shipping is estimated | [etsy.com/developers](https://www.etsy.com/developers) → `ETSY_API_KEY` + `ETSY_SHARED_SECRET` (both required since Jan 2026) |
| **Email alerts** (Gmail, read-only) | **Craigslist, OfferUp, Mercari, FB Marketplace, Swappa**, through their saved-search alert emails | See below |
| **Demo** | Simulated | On automatically when nothing else is configured |

**"Check on ↗" quick links** on every watch open that item's search on Walmart, Amazon, eBay, Newegg, Best Buy, FB Marketplace, OfferUp, Mercari, and Craigslist, using your max price/ZIP where the site supports it. No keys needed. Those results stay on the site; they aren't pulled into the app or alerts.

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
3. In **Google Auth Platform** (it used to be called "OAuth consent screen"), add the `gmail.readonly` scope under **Data Access**. The app stays in **Testing**: go to **Audience → Test users** and add each person's Gmail **before** they try to connect, or Google blocks them (up to 100 people). Publishing it for everyone would need Google's security review, because Gmail is a restricted scope.
   - **In Testing, Google ends each Gmail connection after 7 days.** The app then shows "⚠ Gmail connection expired: Reconnect" in Settings; it's one click to reconnect.
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

Data is kept in SQLite (`sniper.db`). Back it up together with `.env`, which holds
`SNIPER_SECRET_KEY` (setup puts it there; older installs may have a `.sniper_secret` file instead).
Without that key, saved Gmail connections can't be read.

The app writes a backup to `backups/` every day (database + `.env` + `.sniper_secret` + `keys/`, newest 14 kept),
or run `python -m sniper.admin backup` any time. Use these instead of copying `sniper.db` by hand: while the app
runs, recent changes can still be in `sniper.db-wal`, so a plain copy can miss them.

**Restoring a backup:** stop the app, unpack the newest `backups/price-sniper-backup-….tar.gz` into the
Price-Sniper folder (it holds `sniper.db`, `.env`, `.sniper_secret`, `keys/`), delete any old `sniper.db-wal` and
`sniper.db-shm` files, then start the app again. To unpack: `tar -xzf <file>.tar.gz` (works in PowerShell on
Windows 10/11 too).

## Updating with a heads-up for users

```bash
scripts/update.sh            # 60-second warning, then pull, install, test, restart
scripts/update.sh 120 "Adding Newegg, back in 2 min"
```

Every open page shows a countdown banner ("Update in 0:45. Finish what
you're doing"), then "Updating now…". Each page reloads by itself when the
new version answers, and users stay signed in. If the pull, install, or tests
fail, the script cancels the banner, puts the code back the way it was, and
leaves the old version running. It restarts whichever way setup installed the
app (the `price-sniper` user service on Linux, launchd on Mac); for a system
service with another name, set `SNIPER_SERVICE`.

To do it by hand:
`python -m sniper.maintenance announce --in 60` · `cancel` · `status`.
From CI or another machine: set `SNIPER_ADMIN_TOKEN`, then
`curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"seconds":60}' https://your-app/api/admin/maintenance`.

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
  maintenance.py  update heads-up notice (CLI + used by the banner)
  sources/      ebay, bestbuy, walmart, reverb, etsy, gmail (alert emails), demo
```
