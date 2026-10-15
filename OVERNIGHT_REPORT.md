# Overnight report (Oct 15, 2026)

Everything below is on the **`overnight-improvements`** branch. Your `main` version is untouched.
If you like it, tell Claude "merge the overnight branch" (or merge it on GitHub). If not, nothing changes.

**At a glance:** 46 commits · 49 files changed · tests went from 54 → **90** (all passing) · zero accessibility violations · zero errors in a long unattended run.

---

## 🐛 Bugs fixed

| # | Problem | What changed |
|---|---|---|
| 1 | **Laptops, prebuilt PCs, lots and bundles counted as the GPU** (and inflated "typical price") | For PC-part searches, titles that are laptops/desktops/prebuilts/lots/bundles, or that also name a CPU (= a whole PC), are skipped. "RTX 4060 laptop" still finds laptops if you ask for them |
| 2 | **r/hardwareswap read the old, crossed-out price** (`~~$260~~ $230` → $260) | Reads the current price only |
| 3 | **r/hardwareswap showed SOLD items** | Crossed-out lines and lines saying SOLD / pending / traded are skipped |
| 4 | **Etsy would fail every search**: since Jan 18, 2026, Etsy requires the shared secret | Both keys required, docs updated |
| 5 | **eBay location header format**: your ZIP may have been ignored for shipping costs | Sent URL-encoded, the way eBay expects |
| 6 | **eBay's 2025 "Pre-owned" condition codes** (2990, 3010) were missing | Added |
| 7 | **Editing a watch's target never alerted** on a listing it already knew at that price | Changing max/target re-checks known listings once |
| 8 | **Failed phone notifications were silently ignored** (ntfy.sh rate-limits with "429") | Failures are detected, shown in Settings, and explained by the test button |
| 9 | **Gmail connections die every 7 days** in Google's testing mode, with a confusing error forever | Shows "⚠ Gmail connection expired: Reconnect" instead |
| 10 | **One slow site could stall everyone's checks** | Each site gets 60 seconds max, then the check moves on |
| 11 | **Relisted items alerted again as "new"** | Same seller + same title + similar price within 7 days = same item |
| 12 | **eBay/Reddit kept using a rejected key** for up to 2 hours | Gets a fresh key and retries once |
| 13 | **Running the app twice would double every alert** | Only one copy runs the checks |
| 14 | **The database grew forever, with no backups** | Daily cleanup + daily backup (keeps 14) |
| 15 | **Phone layout scrolled sideways** (long titles, smallest phones) | Checked at 320 / 390 / 768 / 1280 px: no sideways scrolling anywhere |
| 16 | **Reddit's naming rule**: apps that don't identify themselves get throttled | Uses Reddit's required format (add `REDDIT_USERNAME`) |
| 17 | **Foreign prices counted as dollars**: Etsy and Reverb show the *seller's* currency, so £200 looked like $200 | Only US-dollar prices are used |
| 18 | **A listing with no price showed up as $0**: "lowest ever" plus a fake "hit your target" alert | Listings without a real price are ignored |
| 19 | **Discord: a listing titled "@everyone" would ping your whole server**; long alerts could cut off the link | Mentions are blocked, and the text gets shortened instead of the link |
| 20 | **Long phone alerts became a file attachment** (ntfy's 4 KB limit) | Kept under the limit |
| 21 | **Signed out after 30 days even if you use it daily** | Stays signed in as long as you open it at least once a month |
| 22 | **"Updating now…" banner could get stuck forever** if an update was announced but never happened | Clears itself 5 minutes after the deadline |
| 23 | **Quiet hours used the wrong time zone on Windows** (Windows doesn't include the time-zone data Python needs) | Added the `tzdata` package, plus a daylight-saving test |
| 24 | **My own new PC-part filter hid real parts** ("Desktop Processor", "Desktop Memory", "SSD for Laptop & Desktop", "with Radeon Graphics"). Found by a second review pass | Those phrases now count as describing the part; laptops/prebuilts/bundles are still skipped. Tested on 27 real store titles |
| 25 | **"Ryzen 5 7600X" couldn't find the 7600X** (read as a RAM kit "7600×6"); "Ryzen 5 7600" matched the 7600X; "iPhone 15 Pro" matched Pro Max | Fixed, with tests |
| 26 | **Editing a watch while it was being checked could undo the edit** (and deleting one mid-check left junk behind) | The check picks up your latest settings and saves only its own results |
| 27 | **r/hardwareswap skipped "sold as-is" / "not sold separately" lines** | Only lines actually marked gone ("SOLD", "sold for $430", "traded to…", "pending") are skipped |
| 28 | **More matching edge cases from a second review**: Ryzen 8000G "Radeon 780M Graphics" rejected as a GPU bundle; "5800X" matched the pricier 5800X3D; used posts saying "pulled from a prebuilt" or "PC & laptop" rejected as whole PCs; "iPhone 15 Pro, Max battery health" read as a Pro Max. A third review then caught "Gaming Laptop PC", "iPhone 15 Plus" and "Pro-Max" slipping through | All fixed, each with a test |
| 29 | **"Metal Backplate" in a card's title got it rejected as junk** | Only backplates sold by themselves are rejected |
| 30 | **"Database is locked"** possible when running an admin/update command while the app runs | Database switched to SQLite's WAL mode |
| 31 | Walmart could alert on out-of-stock items; login tracker could grow without limit; muted/hidden listings still counted in the watch list; a few others | Fixed |

## ✨ New and improved (polish + visuals)
- **🏆 "Lowest ever"** and **NEW** badges (new since your last visit; watches with new finds get a NEW tag too).
- **"found 12 min ago · was $254.83"** on every listing, so price drops show at a glance.
- **🔔 Recent alerts** in the top bar, with an unread count. See alerts you missed.
- **Edit a watch** without losing its price history.
- **Compact view** and an instant **"Search these results"** box.
- **Chart:** dashed lines for your max and 🎯 target.
- **Installable app:** icon + "Add to Home Screen" on iPhone/Android.
- **First-run screen** with tips and one-tap examples (RTX 4060, DDR5 2x16 6400, …).
- **Site initials** (eB, BB, CL…) instead of empty gray image boxes.
- **Settings:** *Generate* a safe ntfy topic, **🔔 Send test notification**, **Delete my account**, and a **privacy page** (eBay's rules require one when showing seller names).
- **Scam check:** also flags "small deposit to hold", money orders, "deployed / overseas" stories and "codes only", and multi-option eBay listings where the price may not be for this item. Honest "no deposits, no holds" wording is *not* flagged.
- **Accessibility:** passes WCAG 2.2 AA checks (axe) on every screen, in light and dark mode. That covers contrast, labels, tap sizes and keyboard focus.
- **Security:** security headers, cross-site requests blocked, listing links must be real web links.
- **Desktop:** the New watch form and watch list stay on screen while you scroll results (screenshot 32).
- **Phones:** after *Start watching* you're taken straight to the results; **↑ Top** button on long lists; unread alerts are tracked per account on shared computers.
- **Phones:** a *Switch watch* menu and a **＋ New watch** button at the top, so you don't scroll past every result (screenshot 31).
- **Browser tab** shows unread alerts, e.g. "(3) Price Sniper"; results refresh as soon as you come back to the tab.
- **Pop-ups** stay open while you point at them, and have a ✕ close button. Screen readers no longer read the update countdown out every second.
- **Pinned library versions** (`constraints.txt`), so a library update can't break your server by surprise.

Screenshots: `docs/screenshots/25` through `32`.

Also checked: everything installs and passes on **Python 3.11** (the version on Debian 12 / Google Cloud's free server), not just the newest Python.

## 🔎 Research findings that affect you
- **Google Cloud may not be 100% free.** Since 2024 Google charges about **$3.60/month for a public IP address**. Some say the free tier covers it, but others got billed. Also, the console's default disk type ("Balanced") **is billed**; you need "Standard". The new **CLOUD_SETUP.md** walks through every setting and has you set a $1 budget alert. ([Google](https://cloud.google.com/vpc/pricing-announce-external-ips), [discussion](https://lowendspirit.com/discussion/comment/26480))
- **Google's free server has only 1 GB of memory.** The guide adds a swap file first, and the setup script warns if it's missing.
- **Email alerts:** Craigslist officially supports them. OfferUp maybe. Facebook seems to alert only inside its app. So the Gmail feature mainly helps with Craigslist.
- **Tailscale sharing** (giving friends access to just the one computer) is a *beta* feature. The guide now includes a fallback: invite them to your network instead.
- **Reverb** search reportedly works with no account. Set `REVERB_ENABLED=1` to try it free.

## ⚠️ Still untested (needs the real server/keys)
- Real eBay / Reddit / Best Buy / Etsy responses. Everything was checked against docs and realistic samples, but never live.
- Phone push via ntfy, and Gmail sign-in.
- The **Windows** script was syntax-checked and passed Microsoft's PowerShell analyzer, but was never run on Windows.
- Auto-start (systemd / launchd / Task Scheduler) on a real machine.

## 🙋 Decisions for you
1. **Merge the overnight branch into main?**
2. **Hosting:** Google Cloud (probably free, possibly ~$3.60/mo for the IP), or a ~$4–6/mo server, or an always-on PC if one turns up?
3. When you make the **Reddit** app, also put your Reddit username in `.env` as `REDDIT_USERNAME`.
