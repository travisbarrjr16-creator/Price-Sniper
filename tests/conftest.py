"""Keep tests independent of the real .env: no real API keys, no invite code, no https-only cookies."""
import os

os.environ["SNIPER_NO_DOTENV"] = "1"
for key in list(os.environ):
    if key.startswith(("SNIPER_", "EBAY_", "BESTBUY_", "WALMART_", "REDDIT_", "REVERB_", "ETSY_", "GOOGLE_", "NTFY_")) \
            and key != "SNIPER_NO_DOTENV" or key in ("PUBLIC_URL", "DISCORD_WEBHOOK_URL"):
        del os.environ[key]

os.environ.pop("ANTHROPIC_API_KEY", None)    # photo tests never call the real vision API
os.environ["SNIPER_HOUSEKEEPING"] = "0"     # no daily backups written while tests run the loop
