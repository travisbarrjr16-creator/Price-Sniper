# Bug research: what goes wrong in apps like Price Sniper

Researched Oct 15, 2026: known bugs and failure modes in price trackers, deal-alert bots, apps built on marketplace APIs, push-notification apps, and small multi-user web apps. Each one was then **checked against our code**.

- ✅ = we already handle it
- 🔴 = confirmed problem in our app (tested)
- 🟠 = likely problem / gap
- 🟡 = minor or "good to have"

---

## 🔴 Confirmed problems (tested against our code)

### 1. Laptops, whole PCs, bundles, and lots count as "an RTX 4060"
Searching "RTX 4060" matched all of these in a test:
- *ASUS TUF Gaming **Laptop** RTX 4060*
- *Gaming **PC** Ryzen 5 RTX 4060*
- ***Lot of 2** RTX 4060*
- *RTX 4060 **+ Ryzen bundle***
- *iBUYPOWER **Prebuilt Desktop** RTX 4060*

**Why it matters:**
- Laptops and PCs cost far more than the card alone, so they push the **"typical price" up**. That makes ordinary prices look like "great deals."
- A cheap laptop under your max could still send an alert.
- This is the #1 complaint about price trackers: alerts for the wrong product or variant. A review of one tracker found it pulling a different edition of the product ([Macworld](https://www.macworld.com/article/228092/yellowbag-review-don-t-shop-until-the-price-drops-on-this-ios-app.html)).

### 2. Reddit r/hardwareswap price drops read the *old* price
Sellers lower prices by crossing out the old one: `~~$260~~ $230`. Our parser read **$260**.

### 3. Reddit r/hardwareswap items marked SOLD still show up
Sellers cross out sold items: `~~MSI RTX 4060 - $230~~ SOLD`. Our parser **still listed it at $230**, so you could get an alert for something already gone.

---

## 🟠 Likely problems / gaps

### 4. Gmail connections will die every 7 days
While a Google app is in "Testing" mode (which ours will be), **Google expires refresh tokens after 7 days**. After that, the person must reconnect ([Unipile](https://www.unipile.com/google-oauth-refresh-token/), [example write-up](https://tech.sailjada.com/posts/2026-05-06-2046.html)).
- **Our app:** it would show a cryptic "400 error" on the watch every check, forever, and never tell the user to reconnect.
- **Fix:** detect the expired connection, mark Gmail as disconnected, and show "Reconnect Gmail" in Settings.
- **Long-term fix:** publishing the Google app removes the 7-day limit. Gmail is a restricted scope, so that needs Google's review.

### 5. eBay "multi-variation" listings can show a fake-low price
One eBay listing can hold several items (for example, a GPU *and* a $5 cable). Search results can show a variation's price that isn't the product you want. Sellers abuse this to rank low ([ChannelX](https://channelx.world/2011/03/how-multi-variation-prices-are-displayed-in-search-results/), [eBay community](https://community.ebay.com/t5/Selling/Poll-strict-rules-for-listings-with-variations/m-p/28239942)).
- **Our app:** the scam check would probably flag a $5 "RTX 4060" as "price far below typical." But we don't specifically recognize variation listings.
- **Fix:** treat eBay listings that have an item group as "price from …" and check them more carefully, or skip them.

### 6. Relisted items alert again as "new"
Deal-alert services commonly send duplicates when the same item reappears under a new ID ([Slickdeals threads](https://slickdeals.net/f/16259380-duplicate-triplicate-email-deal-alerts), [Vector](https://learn.vector.co/articles/7698806292-solving-for-multiple-notifications-for-one-visitor)).
- **Our app:** if a seller ends and relists, it gets a new ID and alerts again.
- **Fix:** if the same seller + same title + similar price was seen in the last ~7 days, treat it as the same item.

### 7. No overall time limit per site
Every request has a 20-second timeout. But a site that responds *very slowly* can still stall a check, and checks run one after another, so one slow site delays everyone's watches ([asyncio pitfalls](https://blog.codercops.com/blog/python-asyncio-production-pitfalls-2026), [Python forum](https://discuss.python.org/t/help-with-asyncio-program-freezing-during-requests/8542)).
- **Fix:** give each site's search a hard limit (for example, 60 seconds), then move on with whatever the other sites returned.

### 8. eBay key not renewed after a rejection
We renew the eBay access key on schedule (every ~2 hours). But if eBay rejects it early (401), we keep using the bad key until the scheduled renewal.
- **Fix:** on a 401, get a new key and retry once. Same for Reddit.

### 9. The database grows forever
Old listings and price-history rows are never cleaned up. With 6 users and about 25 watches, that's tens of thousands of rows a day. It's fine for months, but it slowly gets bigger and slower, and the free Google server only has 30 GB.
- **Fix:** delete listings gone for 60+ days and history older than ~1 year. Add a simple nightly backup of `sniper.db` + `.env` + `keys/`.

### 10. Walmart: out-of-stock items aren't filtered
Price trackers often alert on items that are out of stock ([Chrome Web Store reviews](https://chrome.google.com/webstore/detail/amazon-price-alert/detail/amazon-price-alert/nlmoemlbohbcjdobfebkmjpkkdbgnbki)).
- **Our app:** Best Buy is filtered to in-stock. Walmart isn't.
- Low priority, since Walmart is on hold.

---

## 🟡 Minor / good to have

### 11. ntfy topics are public by obscurity
On ntfy.sh, **anyone who guesses your topic name can read your alerts** ([Oh Dear docs](https://ohdear.app/docs/notifications/ntfy), [Railway guide](https://railway.com/deploy/ntfy-push-notifications)).
- **Our app:** we only require 6+ characters, so something like `travis1` would be easy to guess.
- **Fix:** add a "Generate random topic" button and require 12+ characters. The alerts only contain deal info, nothing secret.

### 12. eBay rules: privacy policy link
eBay's license says apps that show seller usernames must **link to a privacy policy** ([eBay API license](https://developer.ebay.com/products/license)).
- **Fix:** add a short privacy page (what's stored, who can see it).

### 13. Scam patterns to add to the risk check
Documented GPU scams from 2025–2026 ([PCWorld](https://www.pcworld.com/article/2599209/ebay-newest-nvidia-gpu-scams-are-even-dumber-than-before.html), [Gigazine](https://www.gigazine.net/gsc_news/en/20250206-nvidia-gpu-scams-sumber), [Guru3D](https://www.guru3d.com/story/rtx-5090-on-amazon-sparks-scam-reports-and-refund-disputes/), [Evetech](https://evezone.evetech.co.za/deep-dives/deposit-first-sellers-how-gpu-listing-scams-hook-buyers)):
- **Already caught ✅:** "photo of" / "box only" listings, far-below-typical prices, brand-new sellers, and Zelle/wire/gift-card-only payment.
- **Could add:**
  - "deposit" / "hold it for you" wording
  - "read description" combined with a suspiciously low price
  - new-seller **at or just above retail** (the "bot bait" pattern)

### 14. Price formats we don't read
- **"$1.2k" / "1k":** rare for GPUs, but common for pricier items.
- **"$200–$300" ranges:** we take the first number.

(European `1.234,56` formats don't matter here, since all our sources are US.)

### 15. Email alert format changes
If Craigslist changes its email layout, our parser would silently find 0 listings.
- **Fix:** show a warning when alert emails arrive but nothing could be read from them.

---

## ✅ Already handled (checked)

| Known problem in similar apps | Our app |
|---|---|
| Wrong product variant (4060 vs 4060 Ti) | Exact-model filter ✅ |
| "Empty box" / "photo of" scams | Junk filter + risk score ✅ |
| Price without shipping/tax makes "deals" fake | Total = price + shipping + tax ✅ |
| Same listing alerting over and over | Alerts only on new / price-dropped, remembers last alerted price ✅ |
| Notification floods | One push per check (digest) + quiet hours ✅ |
| Out-of-stock alerts (Best Buy) | In-stock filter ✅ |
| Expired deal posts (r/buildapcsales) | "Expired" flair skipped ✅ |
| SQLite "database is locked" errors ([source](https://oneuptime.com/blog/post/2026-09-08-fix-sqlite-database-is-locked-concurrent-writes/markdown)) | Single shared connection + lock; reads locked too (fixed earlier) ✅ |
| Background task silently dies ([source](https://python.plainenglish.io/7-asyncio-memory-leaks-silently-destroying-production-python-systems-fix-before-its-too-late-4cf724ea1174)) | Task kept referenced; one bad watch can't stop others (fixed today) ✅ |
| Every HTTP call needs a timeout | 20 s on every client ✅ (overall limit: see #7) |
| Daylight-saving bugs in schedules ([source](https://dev.to/cronmonitor/handling-timezone-issues-in-cron-jobs-2025-guide-52ii)) | Schedules use UTC; quiet hours are checked in the user's own time zone at send time ✅ |
| Running over API rate limits ([Reddit limits](https://redditwarp.readthedocs.io/en/latest/user-guide/rate-limits.html)) | Daily budget per site, planned + counted ✅ |
| CSRF on cookie logins ([FastAPI CVE-2021-32677](https://security.archlinux.org/CVE-2021-32677)) | FastAPI 0.143 (fixed version), SameSite=Lax cookies, JSON-only API, Tailscale-only access ✅ |
| XSS from listing titles | Everything escaped; tested with a malicious item name ✅ |
| Users seeing each other's data | Ownership checks on every route; tested ✅ |
| Wild input crashing the server | Input validated; tested ✅ |

---

## Recommended fix order
1. **#1** Laptops/PCs/bundles/lots filter (biggest effect on alert quality)
2. **#2 + #3** r/hardwareswap crossed-out prices and SOLD items
3. **#4** Gmail 7-day expiry → clear "Reconnect" prompt
4. **#7 + #8** per-site time limit, retry with a fresh key on 401
5. **#6** relisted-item dedupe
6. **#9** cleanup + nightly backup
7. **#11, #12, #13** small polish
8. **#5, #10, #14, #15** when real data shows they matter
