#!/usr/bin/env python3
"""OPS_QuickBuild — account importer 👥

Reads a text file of `username:password` (one per line) and creates real
bancho.py accounts: users row + stats rows for every game mode, with the
exact password scheme osu!stable expects (bcrypt of the md5 of the password).

Optional third field per line: `username:password:admin` grants staff privs.

Usage:
    import_accounts.py --bancho /opt/myserver/bancho.py --accounts accounts.txt
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

R = "\033[0m"; B = "\033[1m"; GRN = "\033[32m"; YLW = "\033[33m"; RED = "\033[31m"; CYN = "\033[36m"

# bancho.py privilege bits
PRIV_UNRESTRICTED = 1
PRIV_VERIFIED = 2
PRIV_MODERATOR = 16
PRIV_ADMINISTRATOR = 32
PRIV_DEVELOPER = 64
PRIV_NORMAL = PRIV_UNRESTRICTED | PRIV_VERIFIED
PRIV_ADMIN = PRIV_NORMAL | PRIV_MODERATOR | PRIV_ADMINISTRATOR | PRIV_DEVELOPER

USERNAME_RE = re.compile(r"^[\w \[\]-]{2,15}$")
STAT_MODES = (0, 1, 2, 3, 4, 5, 6, 8)  # vn!std/taiko/catch/mania, rx!std/taiko/catch, ap!std


def load_env(env_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def main() -> int:
    ap = argparse.ArgumentParser(description="import accounts into a bancho.py database")
    ap.add_argument("--bancho", required=True, help="path to the bancho.py directory (with .env)")
    ap.add_argument("--accounts", required=True, help="path to username:password file")
    args = ap.parse_args()

    bancho = Path(args.bancho)
    env = load_env(bancho / ".env")

    # run under bancho.py's venv so these are guaranteed present
    import bcrypt
    import pymysql

    conn = pymysql.connect(
        host=env.get("DB_HOST", "127.0.0.1"),
        port=int(env.get("DB_PORT", "3306")),
        user=env["DB_USER"],
        password=env["DB_PASS"],
        database=env["DB_NAME"],
        charset="utf8mb4",
        autocommit=False,
    )
    cur = conn.cursor()

    lines = Path(args.accounts).read_text(encoding="utf-8-sig").splitlines()
    created = skipped = failed = 0
    now = int(time.time())

    print(f"\n{B}👥 importing accounts from {args.accounts}{R}\n")

    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(":")
        if len(parts) < 2:
            print(f"  {RED}❌ line {lineno}: not 'username:password' format, skipping{R}")
            failed += 1
            continue

        name = parts[0].strip()
        password = parts[1]
        is_admin = len(parts) > 2 and parts[2].strip().lower() in ("admin", "staff", "owner")

        if not USERNAME_RE.match(name):
            print(f"  {RED}❌ line {lineno}: username '{name}' invalid (2-15 chars, letters/numbers/_ -[]){R}")
            failed += 1
            continue
        if len(password) < 3:
            print(f"  {RED}❌ line {lineno}: '{name}' password too short (3+ chars){R}")
            failed += 1
            continue

        safe_name = name.lower().replace(" ", "_")
        cur.execute("SELECT id FROM users WHERE safe_name = %s", (safe_name,))
        if cur.fetchone():
            print(f"  {YLW}⏭️  '{name}' already exists, skipping{R}")
            skipped += 1
            continue

        # osu!stable sends md5(password); bancho.py stores bcrypt(md5(password))
        pw_md5 = hashlib.md5(password.encode()).hexdigest().encode()
        pw_bcrypt = bcrypt.hashpw(pw_md5, bcrypt.gensalt())
        priv = PRIV_ADMIN if is_admin else PRIV_NORMAL

        try:
            cur.execute(
                "INSERT INTO users (name, safe_name, email, priv, pw_bcrypt, country,"
                " creation_time, latest_activity)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (name, safe_name, f"{safe_name}@ops.local", priv, pw_bcrypt, "xx", now, now),
            )
            user_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO stats (id, mode) VALUES (%s, %s)",
                [(user_id, mode) for mode in STAT_MODES],
            )
            conn.commit()
            badge = f" {CYN}👑 admin{R}" if is_admin else ""
            print(f"  {GRN}✅ '{name}' created (id {user_id}){badge}{R}")
            created += 1
        except Exception as exc:
            conn.rollback()
            print(f"  {RED}❌ '{name}' failed: {exc}{R}")
            failed += 1

    conn.close()
    print(f"\n{B}📊 done:{R} {GRN}{created} created{R}, {YLW}{skipped} skipped{R}, {RED}{failed} failed{R} 😎\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
