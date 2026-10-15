# Price Sniper: progress so far

A personal deal aggregator. You enter an item (for example "RTX 4060"), a max price, conditions, and a ZIP code and radius. It watches multiple marketplaces in the background, works out what you'd actually pay, filters out wrong models, junk, and scams, tells you whether a price is really a deal, and pushes an alert to your phone with a one-tap link to the listing. Multiple people can each have their own private account.

**Stack:** Python 3 + FastAPI, SQLite, and a single-page web UI in vanilla JS (light/dark mode, works on phones). 27 automated tests pass.

---

## Features built

### Searching & watching
- **Watches** run in the background every N minutes (minimum 5). The first check runs right away when a watch is created.
- Per watch you can set: max total price, conditions (new / open box / refurb / used), ZIP + radius for local pickup, sales tax %, must-include words, exclude words, which sites to search, whether to include auctions, exact-model mode, and hiding scams.
- Pause, resume, delete, and "Check now".

### Unified results
- Every site is mapped to one listing format.
- **Total cost = price + shipping + tax.** Unknown shipping is estimated at $15. Local pickup counts as $0 shipping. Tax is skipped for private local sales.
- Each listing shows site, condition, distance and location (local), and seller name, rating, and feedback count.
- **Sort:** Cheapest (total), Best value, Nearest, Newest.
  - *Best value* adjusts the total for condition (used is worth less than new), scam risk, driving cost to a local pickup, and unknown shipping.
- **Site filter dropdown:** "All sites (23)", "Walmart (5)", and so on, to see one site's results only.
- **One-tap "Open"** button to go straight to the listing on the original site.

### Smart matching (filters out wrong items)
- Every word of the search must be in the title. Handles spellings like `RTX4060` and `8 GB`.
- **Exact-model mode:** "4060" won't match "4060 Ti", "Super", "XT", "Pro", "Max", and similar.
- Drops junk titles: empty box, box only, for parts, not working, backplate, fan only, WTB/wanted, IOU, and so on.

### Scam detection
- A risk score built from:
  - price far below typical
  - a brand-new or low-feedback seller
  - a seller rating under 95%
  - payment red flags ("zelle only", "shipping only", gift cards, "text me", wire transfer, and others)
- Risky listings are hidden by default ("Show hidden scams" toggle) and labeled "Too good to be true".

### "Is this a deal?"
- Each listing is compared to the median total of every non-risky listing seen in the last 30 days, with new and used tracked separately.
- Badges: Great deal (15%+ below typical), Good price, Typical price, Above typical.
- **Chart of lowest price over time** (new vs used), with hover tooltips and a table view.
- Stat tiles: lowest total, typical new, typical used, your max, and how many listings are under your max.

### Alerts
- An alert fires when a listing is **new or drops in price** and either **beats the current lowest** or is **under your max**.
- The first scan sends one summary alert, not a flood. The same listing doesn't alert twice unless its price drops again.
- **Channels (set per user in Settings):**
  - **ntfy** phone push (free app). Tapping the notification opens the listing; "great deal" alerts get urgent priority.
  - Discord webhook.
  - Browser notifications and in-app pop-ups.

### Day-to-day tools (added from Grok's suggestions)
- **🎯 Target price** per watch (separate from max) → urgent alert plus a "Target hit" badge.
- **Clearer alert types:** 🎯 target hit and 🔥 new lowest are urgent; ⬇ price drop and 🆕 new under max are normal unless it's a great deal.
- **✕ Not interested** and **🔇 Mute seller** on every listing, per user, with undo. Hidden ones never alert. They can be managed in Settings.
- **Deal context:** typical price *range* ("$246–$280 · 14 listings"), "N cheaper right now" / "🥇 Cheapest right now", and "Lowest you've seen" per watch.
- **Duplicate watch:** copies all settings into the form so you only change the model name.
- **Quiet hours** per user (in their own time zone), with an option to still buzz for urgent alerts.
- **Password reset without email:** the admin runs `python -m sniper.admin reset-link email` and sends the one-time link (24h, signs out other devices).
- All scam reasons are shown as badges, not just the first one.

### Accounts (multi-user)
- Email + password sign-up and sign-in. Everyone's watches, results, alerts, and settings are private (tested).
- Passwords are hashed (scrypt). Sessions are random tokens stored hashed (30 days). Stored Gmail tokens are encrypted.
- Optional **invite code** so only people you give it to can sign up.
- Login is rate-limited (10 failed tries locks that email for 15 minutes). Each user can have up to 25 watches.
- **Theme button** (🖥 Auto / ☀️ Light / 🌙 Dark) on desktop and phone; remembered per browser.
- **Big red warning** on sign-up and on the Gmail connect step: "USE A NEW EMAIL JUST FOR THIS APP", so alerts don't flood your main inbox.

### Updates without surprising users
- `scripts/update.sh` shows a **big orange 60-second countdown banner** to everyone ("⚠ APP UPDATE IN 0:45 SAVE WHAT YOU'RE DOING!"; it pulses in the last 15 seconds), pulls the new code, installs it, runs the tests, then restarts the app.
- Pages show "Updating now…" and then **reload themselves** when the new version is up. Users stay signed in.
- If the update fails, the banner is cancelled and the old version keeps running.
- It can also be triggered by hand (`python -m sniper.maintenance announce --in 60`) or over HTTP with an admin token (for automated deploys).

---

## Data sources

| Site | How | Status |
|---|---|---|
| **eBay** | Official Browse API (free key) | Built. New/used/refurb + **local pickup within radius**. Not yet tested with real keys. |
| **Best Buy** | Official Products API (free key) | Built. New + refurb. Not yet tested with real keys. |
| **Walmart** | Affiliate API (free walmart.io account, RSA-signed requests) | Built. Request signing is tested. **Unconfirmed whether Walmart still approves new sign-ups.** |
| **Reddit** | Official API (free "script" app) | Built. r/buildapcsales deals (new) + r/hardwareswap used parts. On r/hardwareswap, only selling posts are used, the price is read from the post body, and the seller's trade count counts as feedback. Tested on sample posts, not live yet. |
| **Reverb** | Public API (free personal token) | Built. Music gear, some electronics. |
| **Etsy** | Open API v3 (free key) | Built. Shipping is estimated (Etsy needs an extra call per listing for real shipping). |
| **Craigslist, OfferUp, Mercari, FB Marketplace, Swappa** | **The user's own saved-search alert emails**, read through Gmail (read-only sign-in with Google) | Built. The parser is best-effort and tested on sample emails, **not real ones yet**. Each user connects their own Gmail. |
| **Demo** | Simulated marketplace | On automatically when no keys are set, so the app can be tried right away. |

**Not included, and why:**
- **Amazon:** the API needs an affiliate account with recent sales. Keepa works but is paid.
- **Newegg:** no public API.
- **Discogs:** its API can't search listings for sale.
- **Facebook / OfferUp / Mercari direct search:** no public API, and logging in as the user would break their terms and risk a ban. They're covered through the alert emails instead.
- **eBay sold prices:** the Marketplace Insights API is restricted to approved partners, so deal ratings use asking prices.

---

## Known limitations / not done yet
- None of the real marketplace connections have been run with live keys yet; everything so far was tested on the Demo source.
- **Hosting plan:** free, on an always-on home computer, shared privately over Tailscale (see HOME_SETUP.md). The setup scripts are written for Windows, Mac, and Linux. The Linux/Mac install steps were tested in a sandbox; the Windows script and the actual auto-start (launchd/systemd/Task Scheduler) haven't been run on a real machine yet.
- **Gmail setup:** until Google reviews the app, each user's Gmail has to be added as a "test user" (up to 100 people), because Gmail is a restricted permission.
- Price history only covers what the app has seen since a watch was created; there's no past sold-price data.
- Local distance only comes from eBay. Email-alert listings have no distance.
- Password reset is by admin link only. There's no email sending, on purpose.
- The code isn't on GitHub yet.

---

## Questions for you (Grok): what should we add next?
1. What features would make this more useful day to day?
2. Better ways to judge "is this a deal" without sold-price data?
3. Other **free, legitimate** data sources (APIs, RSS, alert emails) worth adding, especially for **local/used** listings?
4. How to handle password reset without the app sending email (or a cheap, simple way to send it)?
5. Ideas to make scam detection smarter?
6. Simplest cheap way to host this for a handful of friends?
7. Anything that could get the app or its users in trouble with a marketplace's terms that I should watch out for?
