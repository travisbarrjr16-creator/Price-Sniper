# Getting your free site keys

Only **you** (the person running the app) do this, once. Your friends never need any of these accounts. They only use their own store accounts if they buy something after tapping **Open ↗**.

> **Tip: use your deals-only email for every account below** (for example `yourname.deals@gmail.com`). These sites send newsletters and notices, and this keeps them all in one place, out of your main inbox.

Each key goes into the `.env` file in the Price-Sniper folder. After changing `.env`, restart the app (see HOME_SETUP.md, step 8), then check that everything works:
```
.venv/bin/python -m sniper.admin test-sources        # Mac / Linux
.venv\Scripts\python -m sniper.admin test-sources    # Windows
```
It runs one real search per site and prints `OK, 37 listings…` or the exact error.

## Checklist

| | Site | Where | What goes in `.env` | Notes |
|---|---|---|---|---|
| ☐ | **eBay** (most important) | [developer.ebay.com](https://developer.ebay.com) | `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | Use the **Production** keyset, not Sandbox |
| ☐ | **Walmart** | [walmart.io](https://walmart.io) | `WALMART_CONSUMER_ID`, `WALMART_PRIVATE_KEY_PATH` | Steps below. Approval may be needed |
| ☐ | **Reddit** | [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | "create app" → type **script** |
| ☐ | **Best Buy** | [developer.bestbuy.com](https://developer.bestbuy.com) | `BESTBUY_API_KEY` | May reject free email addresses |
| ☐ | **Etsy** | [etsy.com/developers](https://www.etsy.com/developers) | `ETSY_API_KEY` (+ `ETSY_SHARED_SECRET`) | Optional, handmade/vintage |
| ☐ | **Reverb** | reverb.com → Settings → API & Integrations | `REVERB_TOKEN` | Optional, music gear |
| ☐ | **Google** (Gmail alert emails) | [console.cloud.google.com](https://console.cloud.google.com) | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional, for Craigslist/OfferUp/etc. alerts; see README |

Start with **eBay**. It covers the most, including used items and local pickup.

---

## Walmart, step by step

Walmart uses a "key pair" instead of a simple password-style key: a **private key** that stays on your server and a **public key** you give to Walmart. Price Sniper makes both for you.

1. **Make the account.** Go to [walmart.io](https://walmart.io) and sign up with your deals email. Look for the **Affiliate** API / program.
   - Walmart may require you to apply and be approved as an affiliate. It's free, but there can be a wait.
   - It's also possible they no longer approve new individual developers. If so, skip Walmart; everything else still works.
2. **Make the key pair** on the computer or server that runs Price Sniper, from the Price-Sniper folder:
   ```
   .venv/bin/python -m sniper.admin walmart-keys        # Mac / Linux
   .venv\Scripts\python -m sniper.admin walmart-keys    # Windows
   ```
   It prints a **public key**: a block starting with `-----BEGIN PUBLIC KEY-----`. It also saves the private key as `keys/walmart_private.pem` and records that location in `.env` for you.
   - The private key never leaves your computer. It isn't uploaded to GitHub.
3. **Give Walmart the public key.** In the walmart.io dashboard, create an application and paste the **whole** public key block, including the BEGIN/END lines, where it asks for one.
4. **Copy the Consumer ID.** After the upload, walmart.io shows a **Consumer ID**. Put it in `.env`:
   ```
   WALMART_CONSUMER_ID=paste-it-here
   ```
   If it also shows a **key version** other than 1, set `WALMART_KEY_VERSION=` to that number.
5. **Restart the app and test:** run `test-sources` (above). You want to see `Walmart  OK, … listings`.

**If it fails:**
- `HTTP 401` / `403`: the Consumer ID or key version is wrong, the public key wasn't saved on walmart.io, or your application isn't approved yet.
- `not set up`: `.env` is missing the Consumer ID, or the `keys/walmart_private.pem` file isn't where `.env` says.
- Lost the private key: run `walmart-keys --force` and upload the new public key to walmart.io (the old one stops working).
- **Back up `keys/` together with `.env` and `sniper.db`.**

Walmart's own guide: [walmart.io affiliate quickstart](https://walmart.io/apidocs/affiliates/quickstart).
