# Price Sniper: marketplace access problem brief

## What I'm building
A personal deal watcher. I enter an item (for example "RTX 4060"), a max price, conditions (new or used), and a ZIP code and radius. It checks multiple marketplaces in the background, normalizes total cost (price + shipping + tax), filters out wrong models, junk, and scams, rates whether a price is actually a deal, and pushes an alert to my phone with a one-tap link to the listing.

**Already built and working:** Python/FastAPI app, SQLite, web UI, strict model matching (4060 is not 4060 Ti), scam/risk scoring, 30-day price history, and alerts via ntfy, Discord, or the browser. It has a pluggable source interface: each marketplace is one class that returns normalized listings.

**The open problem is data access.** Most marketplaces I want, especially the ones with **local/used** listings, don't offer a public search API.

---

## Source-by-source status

### Works: official, self-serve
| Source | Access | Covers | Limits |
|---|---|---|---|
| **eBay Browse API** | Free developer keys | New, used, refurbished, and **local pickup within a radius of a ZIP code**; shipping cost to my ZIP | Sold/completed prices are only available through the **Marketplace Insights API**, a limited release for approved partners. Most applicants are declined or waitlisted. So "is this a deal" uses asking prices, not sold prices. |
| **Best Buy Products API** | Free key | New and refurbished retail; open-box through the Buying Options API | An older (2016) report says they refused keys to free email addresses such as Gmail. Commercial use needs a partner agreement. Their terms limit caching. |

### Gated or paid
| Source | Situation |
|---|---|
| **Amazon** | Product Advertising API 5.0 was deprecated in 2026 and replaced by the **Creators API**. That requires an Amazon Associates (affiliate) account; one third-party guide says you need about 10 qualified sales in the last 30 days. That's hard for a personal tool. |
| **Keepa** (Amazon data) | Paid monthly API, billed in tokens. It has Amazon prices plus full price history, which is good for deal rating. Amazon only. Legitimate, but it costs money. |
| **Walmart** | The developer portal is for sellers, suppliers, and advertisers. I found no confirmed self-serve consumer product-search API. There may be an affiliate API, which is unconfirmed. Their Terms of Use (updated June 2026) ban robots, spiders, and automated collection without written consent. |

### No public search API
| Source | Situation |
|---|---|
| **Facebook Marketplace** | No Graph API permission for Marketplace search. "Login with Facebook" only gives the app basic profile info. |
| **OfferUp** | No public API. |
| **Mercari** | No public API that I know of. |
| **Craigslist** | No API, and no login needed to search. Its terms ban automated access, and it has sued scrapers (for example 3Taps, around 2013). It does offer **saved-search email alerts** to account holders. |
| **Newegg** | No official product-search or affiliate API found. Only third-party scraping services (Apify actors). |
| **Swappa** | No public API that I know of. |

---

## Ideas considered and why they were rejected

### "Sign in with my account and let the app browse as me"
- For FB, OfferUp, Mercari, Amazon, Walmart, and Newegg, I know of no official sign-in that grants an app search access. So "signing in" would really mean **storing my password or session cookies and driving a headless browser.**
- **Ban risk to my real account.** Meta and Amazon actively detect automation. Losing my Facebook or Amazon account (order history, Prime, Messenger) costs far more than missing a deal.
- **Terms of service violation** on every one of these sites.
- **Security risk:** the app would hold my live credentials or session.
- **Fragile:** it breaks whenever the site changes its layout or adds bot checks.
- Main point: **login was never the obstacle.** These listings are mostly public. What's forbidden is the *automated access*, and logging in doesn't change that. It just ties the violation to my identity.

### Scraping public pages without logging in
- Same terms-of-service problem, plus CAPTCHAs, rate limits, and IP blocks. It breaks often. Third-party scraping services (Apify and similar) just move the terms problem to someone else.

---

## Legitimate workarounds I already have (or plan)
1. **eBay + Best Buy APIs**: already built.
2. **Keepa** for Amazon, if I'm willing to pay.
3. **Read saved-search alert emails through the Gmail API (OAuth, read-only).** Sites like Craigslist (and eBay, possibly OfferUp) email you when a new listing matches a saved search. The app would parse those emails into listings. My marketplace passwords are never stored, I use the sites' own alert feature, and there's no ban risk. Needs a free Google Cloud OAuth client; personal use in "testing" mode needs no Google review. *Unverified: which marketplaces currently send per-listing alert emails, especially OfferUp and Facebook.*
4. **"Sign in with eBay" (OAuth)**: only for account features like watchlist sync and saved searches. Search doesn't need it.

### Recommended plan (Claude's pick)
| Priority | Step | Fills which gap | Cost |
|---|---|---|---|
| 1 | eBay + Best Buy (done) | Most of the used market, eBay local pickup, major retail | Free |
| 2 | Gmail alert-email ingestion | Craigslist and other local/used sites that send saved-search emails | Free |
| 3 | Keepa | Amazon prices + real price history for "is this a deal?" | Paid monthly |
| 4 | Sign in with eBay | Watchlist/saved-search sync (nice-to-have) | Free |

**Result:** this covers retail, eBay used/local, Amazon, and any site that emails saved-search alerts, without storing marketplace passwords and without breaking any site's terms. Facebook, OfferUp, and Mercari stay uncovered unless one of them offers alert emails or a partner program (see questions below).

---

## Questions for you (Grok)
Looking for ideas that are **within each site's terms** and **don't risk my accounts**:
1. Does Facebook Marketplace, OfferUp, Mercari, or Swappa currently offer **email or push alerts for saved searches** that I could ingest through Gmail?
2. Are there **official partner or affiliate programs** for any of these that an individual can actually join and that include search or listing access?
3. Is there an **open, legal aggregator API** that already licenses local/used listing data?
4. Is the **Walmart affiliate API** still open to new individual developers?
5. Is there any realistic path to **sold-price data** besides eBay Marketplace Insights (for example, a licensed dataset)?
6. Any other US marketplace with a **public search API** worth adding for electronics/GPUs?

---

## Sources
- eBay Marketplace Insights (restricted): https://developer.ebay.com/api-docs/buy/marketplace-insights/overview.html
- eBay forum on Insights access: https://community.ebay.com/t5/eBay-APIs-Talk-to-your-fellow/Marketplace-Insights-API-access/m-p/34838736
- Best Buy keys and free email (2016): https://dzone.com/articles/best-buy-will-not-issue-api-keys-to-free-email-acc
- Best Buy API overview: https://providers.apievangelist.com/providers/best-buy/
- Amazon PA-API 5 deprecation: https://affiliate-program.amazon.com/creatorsapi/docs/en-us/paapiv5-deprecation
- PA-API shutdown write-up: https://dev.to/th3nate/amazon-pa-api-v5-is-shutting-down-april-30-2026-here-is-what-changes-at-the-auth-layer-22ek
- Keepa Python client: https://pypi.org/project/keepa
- Walmart developer and terms analysis: https://vorplabs.com/agent-tools/walmart-marketplace-api
- Newegg (scrapers only): https://apify.com/scrapesage/newegg-scraper
