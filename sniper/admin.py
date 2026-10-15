"""Admin commands, run on the server from the Price-Sniper folder.

    python -m sniper.admin reset-link someone@example.com   # one-time password reset link (24h)
    python -m sniper.admin users                            # list accounts
    python -m sniper.admin walmart-keys                     # make the key pair walmart.io asks for
    python -m sniper.admin test-sources                     # try one real search on every site you set up

(Use .venv/bin/python on Mac/Linux or .venv\\Scripts\\python on Windows.)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

from .db import DB

WALMART_KEY_DIR = Path("keys")
WALMART_PRIVATE = WALMART_KEY_DIR / "walmart_private.pem"
WALMART_PUBLIC = WALMART_KEY_DIR / "walmart_public.pem"


def set_env_values(values: dict[str, str], env_path: Path = Path(".env")) -> None:
    """Write KEY=value into .env, replacing an existing line or adding one."""
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    for k, v in values.items():
        if re.search(rf"^{k}=.*$", text, flags=re.M):
            text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
        else:
            text += ("" if text.endswith("\n") or not text else "\n") + f"{k}={v}\n"
    env_path.write_text(text, encoding="utf-8")


def walmart_keys(force: bool = False) -> str:
    """Create an RSA key pair for walmart.io. The private key never leaves this computer."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    if WALMART_PRIVATE.exists() and not force:
        raise SystemExit(f"{WALMART_PRIVATE} already exists. Use --force to replace it "
                         "(you'd then have to upload the new public key to walmart.io).")
    WALMART_KEY_DIR.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    WALMART_PRIVATE.write_bytes(key.private_bytes(       # PKCS#8, which Walmart requires
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(WALMART_PRIVATE, 0o600)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    WALMART_PUBLIC.write_text(public)
    set_env_values({"WALMART_PRIVATE_KEY_PATH": str(WALMART_PRIVATE)})
    return public


async def _test_sources() -> list[tuple[str, str]]:
    import httpx

    from .models import Watch
    from .sources import ALL_SOURCES

    watch = Watch(query="RTX 4060")
    out = []
    async with httpx.AsyncClient(timeout=25) as client:
        for s in ALL_SOURCES:
            if s.key in ("demo", "gmail"):
                continue
            if not s.configured():
                out.append((s.name, "not set up (no keys in .env)"))
                continue
            try:
                found = await s.search(watch, client)
                out.append((s.name, f"OK, {len(found)} listings for 'RTX 4060'"))
            except httpx.HTTPStatusError as e:
                body = e.response.text[:200].replace("\n", " ")
                out.append((s.name, f"FAILED: HTTP {e.response.status_code}. {body}"))
            except Exception as e:  # noqa: BLE001 - report anything to the admin
                out.append((s.name, f"FAILED: {type(e).__name__}: {e}"))
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m sniper.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset-link", help="make a one-time password reset link to send someone")
    r.add_argument("email")
    r.add_argument("--hours", type=int, default=24)
    sub.add_parser("users", help="list accounts")
    w = sub.add_parser("walmart-keys", help="make the key pair walmart.io asks for")
    w.add_argument("--force", action="store_true", help="replace an existing key pair")
    sub.add_parser("test-sources", help="run one real search on every site that has keys")
    args = ap.parse_args(argv)

    if args.cmd == "walmart-keys":
        public = walmart_keys(args.force)
        print("Created a Walmart key pair.")
        print(f"  Private key (stays here, never share it): {WALMART_PRIVATE}")
        print(f"  Public key (also saved to {WALMART_PUBLIC}). Paste ALL of this into walmart.io:\n")
        print(public)
        print("Then copy the Consumer ID walmart.io shows you into .env as WALMART_CONSUMER_ID=...")
        print("(and WALMART_KEY_VERSION if it shows one other than 1), restart the app, and run:")
        print("  python -m sniper.admin test-sources")
        return
    if args.cmd == "test-sources":
        for name, result in asyncio.run(_test_sources()):
            print(f"{name:<10} {result}")
        return

    db = DB()
    if args.cmd == "reset-link":
        user = db.find_user_by_email(args.email)
        if not user:
            raise SystemExit(f"no account for {args.email}")
        token = db.create_reset_token(user.id, args.hours)
        base = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
        print(f"Send this to {user.email} (works once, expires in {args.hours}h):")
        print(f"{base}/#reset={token}")
    else:
        for row in db._read("SELECT id, email, created_at FROM users ORDER BY id"):
            print(f"{row['id']:>4}  {row['email']:<40} joined {row['created_at'][:10]}")


if __name__ == "__main__":
    main()
