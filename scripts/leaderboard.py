#!/usr/bin/env python3
"""OPS_QuickBuild — CLI leaderboard 🏆

Shows the pp leaderboard straight from the database. Restricted players are
hidden, just like on real bancho.

Usage:
    leaderboard.py --bancho /opt/myserver/bancho.py [--mode osu] [--top 25]

Modes: osu taiko catch mania rx!osu rx!taiko rx!catch ap!osu  (or 0-8)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

R = "\033[0m"; B = "\033[1m"; DIM = "\033[2m"
GRN = "\033[32m"; YLW = "\033[33m"; CYN = "\033[36m"; MAG = "\033[35m"; PNK = "\033[95m"

MODES = {
    "osu": 0, "taiko": 1, "catch": 2, "mania": 3,
    "rx!osu": 4, "rx!taiko": 5, "rx!catch": 6, "ap!osu": 8,
}
MODE_NAMES = {v: k for k, v in MODES.items()}
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


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
    ap = argparse.ArgumentParser(description="show the pp leaderboard")
    ap.add_argument("--bancho", required=True, help="path to the bancho.py directory")
    ap.add_argument("--mode", default="osu", help="game mode (default: osu)")
    ap.add_argument("--top", type=int, default=25, help="how many players (default: 25)")
    args = ap.parse_args()

    mode_key = args.mode.lower()
    if mode_key.isdigit():
        mode = int(mode_key)
    elif mode_key in MODES:
        mode = MODES[mode_key]
    else:
        print(f"unknown mode '{args.mode}' 💀 — options: {', '.join(MODES)} (or 0-8)")
        return 1

    env = load_env(Path(args.bancho) / ".env")
    import pymysql

    conn = pymysql.connect(
        host=env.get("DB_HOST", "127.0.0.1"), port=int(env.get("DB_PORT", "3306")),
        user=env["DB_USER"], password=env["DB_PASS"], database=env["DB_NAME"],
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT u.name, u.country, st.pp, st.acc, st.plays, st.max_combo "
        "FROM stats st JOIN users u ON u.id = st.id "
        "WHERE st.mode = %s AND u.priv & 1 AND u.id != 1 "
        "ORDER BY st.pp DESC, st.rscore DESC LIMIT %s",
        (mode, args.top),
    )
    rows = cur.fetchall()
    conn.close()

    title = MODE_NAMES.get(mode, str(mode))
    print(f"\n{PNK}{B}🏆 {env.get('DOMAIN', 'server')} leaderboard — {title}{R}\n")
    if not rows:
        print(f"  {DIM}nobody has set a score yet... crickets 🦗 go play something{R}\n")
        return 0

    print(f"  {B}{'#':>4}  {'player':<16} {'cc':<3} {'pp':>8} {'acc':>8} {'plays':>7} {'combo':>6}{R}")
    print(f"  {DIM}{'─' * 60}{R}")
    for rank, (name, country, pp, acc, plays, combo) in enumerate(rows, 1):
        medal = MEDALS.get(rank, f"{rank:>2}.")
        color = YLW if rank <= 3 else (CYN if rank <= 10 else "")
        print(f"  {medal:>4}  {color}{name:<16}{R} {country:<3} "
              f"{B}{int(pp):>7,}pp{R} {acc:>7.2f}% {plays:>7,} {combo:>5,}x")
    print(f"\n  {DIM}😎 {len(rows)} players shown • recalc-pp.sh to rebuild after pp changes{R}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
