"""Admin commands, run on the server.

    python -m sniper.admin reset-link someone@example.com   # one-time password reset link (24h)
    python -m sniper.admin users                            # list accounts
"""
from __future__ import annotations

import argparse
import os

from .db import DB


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m sniper.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset-link", help="make a one-time password reset link to send someone")
    r.add_argument("email")
    r.add_argument("--hours", type=int, default=24)
    sub.add_parser("users", help="list accounts")
    args = ap.parse_args(argv)
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
