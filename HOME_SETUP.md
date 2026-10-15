# Run Price Sniper at home, free, for you + up to 5 friends

**The setup:** the app runs on a computer at your place that stays on. **Tailscale** (free) gives you and your friends a private, secure connection to it from anywhere, without opening your home network to the internet.

**Cost:** $0. Tailscale's free Personal plan allows 6 people (you + 5), per its 2026 pricing. Check [tailscale.com/pricing](https://tailscale.com/pricing) in case that changes.

---

## 1. Pick the computer and keep it awake

Any Windows PC, Mac, or Linux machine that can stay on and plugged in works: an old laptop, a desktop, or a Raspberry Pi. Five users is a tiny load.

Stop it from sleeping (the screen can still turn off):
- **Windows:** Settings → System → Power → *When plugged in, put my device to sleep after* → **Never**
- **Mac:** System Settings → Energy (or Battery → Options) → turn on **Prevent automatic sleeping when the display is off**
- **Linux:** `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`

If it's a laptop, keep it plugged in.

## 2. Install Python and Git (if you don't have them)
- **Python 3.10+:** [python.org/downloads](https://www.python.org/downloads/). **Windows:** on the first installer screen, tick **"Add python.exe to PATH"**.
- **Git:** [git-scm.com/downloads](https://git-scm.com/downloads) (Mac: run `git --version` and it offers to install).

## 3. Download the app

Open a terminal (**Windows:** PowerShell · **Mac:** Terminal) and run:
```
git clone https://github.com/travisbarrjr16-creator/Price-Sniper.git
cd Price-Sniper
```
Use `git clone` rather than "Download ZIP"; updates need it. If the repo is private, Git asks you to sign in to GitHub the first time.

## 4. Install Tailscale on that computer
1. Download it from [tailscale.com/download](https://tailscale.com/download), install, and **sign in** (Google, Microsoft, GitHub, or Apple account).
2. Turn on secure web addresses: in the [admin console](https://login.tailscale.com/admin/dns), go to **DNS** → **Enable HTTPS** (HTTPS Certificates).

## 5. Run the setup script

From the `Price-Sniper` folder:

- **Mac / Linux:**
  ```
  scripts/home/setup.sh
  ```
- **Windows (PowerShell):**
  ```
  powershell -ExecutionPolicy Bypass -File scripts\home\setup.ps1
  ```

It takes about a minute. The script:
- installs the app in its own folder (it doesn't touch the rest of your computer),
- creates your settings file (`.env`) with new secret keys and an **invite code**,
- runs the self-tests,
- sets the app to **start automatically**,
- shares it over Tailscale (`tailscale serve`).

At the end it prints something like:
```
For you + friends:  https://your-pc.tail1234.ts.net
Invite code for sign-up:  lucky-hawk-ember-42
```
It's safe to run again any time; it keeps your settings.

## 6. Make your account
Open that `https://...ts.net` address, click **Create an account**, and use the invite code. Use the address it printed, not `localhost`, so sign-in cookies work.

## 7. Add your friends
For each friend:
1. They install the **Tailscale** app on their phone (App Store / Google Play) and make a free account.
2. You let them in. Pick one:
   - **Share just this computer (recommended):** [admin console → Machines](https://login.tailscale.com/admin/machines) → the **⋯** menu next to this computer → **Share** → send them the invite link. They can reach only the app's computer, nothing else of yours.
   - **Or invite them to your network:** admin console → **Users** → **Invite users**. Counts toward the 6-person limit.
3. They open the `https://...ts.net` address, create an account with the invite code, and set their own ntfy topic in **Settings** for phone alerts.

Tip: on a phone, use **Share → Add to Home Screen** so the app gets an icon like a normal app.

## 8. Add marketplace keys (when you're ready)
Follow **[SITE_KEYS.md](SITE_KEYS.md)**: a checklist of the free developer accounts (start with eBay), with step-by-step Walmart instructions. Put the keys in `.env`, then restart:
- **Mac:** `launchctl kickstart -k gui/$(id -u)/com.pricesniper.app`
- **Linux:** `systemctl --user restart price-sniper`
- **Windows:** `Stop-ScheduledTask "Price Sniper"; Start-ScheduledTask "Price Sniper"`

Until you add keys, the app uses the **Demo** data so you can try everything. To check your keys work: `.venv/bin/python -m sniper.admin test-sources` (Windows: `.venv\Scripts\python -m sniper.admin test-sources`).

## 9. Updating
Get the new code onto GitHub (for example, from a Claude Code session), then on the home computer:
- **Mac / Linux:** `scripts/update.sh`
- **Windows:** `powershell -ExecutionPolicy Bypass -File scripts\update.ps1`

Everyone using the app gets the orange 60-second countdown, then it updates and their page reloads. If anything fails, the banner is cancelled and the old version keeps running.

## 10. Good to know
- **Backups:** every so often, copy `sniper.db` (everyone's watches and accounts), `.env` (your keys), and the `keys/` folder (Walmart key) somewhere safe. Without `.env`, saved Gmail connections can't be read.
- **Windows restarts:** the app starts when you **sign in** to Windows. If the PC reboots (for example, Windows Update), sign in again, or set Windows to sign in automatically.
- **Logs if something's wrong:**
  - **Mac:** `logs/sniper.log`
  - **Linux:** `journalctl --user -u price-sniper -f`
  - **Windows:** run `.venv\Scripts\python -m uvicorn sniper.app:app --port 8000` in PowerShell to see errors live (stop the scheduled task first).
- **Forgot password:** run `python -m sniper.admin reset-link their@email` (on Windows `.venv\Scripts\python`, on Mac/Linux `.venv/bin/python`) and send them the link.
- **Security:** the app only listens on the computer itself. The only way in from outside is Tailscale, and only for people you've shared it with. The invite code is a second lock on sign-ups.
