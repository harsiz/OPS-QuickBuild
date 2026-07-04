#!/usr/bin/env python3
"""OPS_QuickBuild — full pp recalculator 🧮

Recalculates pp for EVERY submitted score using bancho.py's own performance
module (so values match live play 1:1), runs each result through your custom
pp profile (ops_custom_pp.py), then rebuilds every player's total pp / acc
and pushes fresh Redis leaderboards.

Run me after editing ops_custom_pp.py, after a bancho.py update, or whenever
the rankings feel cursed.

Usage:
    recalc_pp.py --bancho /opt/myserver/bancho.py [--mode 0] [--quiet]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

R = "\033[0m"; B = "\033[1m"; GRN = "\033[32m"; YLW = "\033[33m"
RED = "\033[31m"; CYN = "\033[36m"; MAG = "\033[35m"; DIM = "\033[2m"

RANKED, APPROVED = 2, 3  # map statuses that award pp (same as bancho)
BEST_SCORE_STATUS = 2    # scores.status: personal best
TOP_N = 100              # scores that count toward total pp
WEIGHT = 0.95            # bancho weighting curve


def load_env(env_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def extract_pp(result: object) -> float | None:
    """Pull the pp value out of a bancho.py performance result, whatever shape it is."""
    if isinstance(result, dict):
        perf = result.get("performance", result)
        if isinstance(perf, dict) and isinstance(perf.get("pp"), (int, float)):
            return float(perf["pp"])
    perf = getattr(result, "performance", result)
    pp = getattr(perf, "pp", None)
    if isinstance(pp, (int, float)):
        return float(pp)
    return None


def build_score_params(params_cls, kwargs: dict):
    """Construct ScoreParams, dropping any kwargs this bancho.py version doesn't know."""
    accepted = set(getattr(params_cls, "__annotations__", {}) or [])
    if not accepted:
        return params_cls(**kwargs)
    return params_cls(**{k: v for k, v in kwargs.items() if k in accepted})


def ensure_osu_file(osu_dir: Path, map_id: int) -> Path | None:
    path = osu_dir / f"{map_id}.osu"
    if path.exists() and path.stat().st_size > 0:
        return path
    try:
        with urllib.request.urlopen(f"https://osu.ppy.sh/osu/{map_id}", timeout=15) as resp:
            data = resp.read()
        if data:
            path.write_bytes(data)
            return path
    except Exception:
        pass
    return None


def bar(done: int, total: int, width: int = 32) -> str:
    filled = int(width * done / max(total, 1))
    return f"{GRN}{'█' * filled}{DIM}{'░' * (width - filled)}{R}"


def main() -> int:
    ap = argparse.ArgumentParser(description="recalculate all pp + rebuild rankings")
    ap.add_argument("--bancho", required=True, help="path to the bancho.py directory")
    ap.add_argument("--mode", type=int, default=None, help="only recalc one mode (0-8)")
    ap.add_argument("--quiet", action="store_true", help="less spam")
    args = ap.parse_args()

    bancho = Path(args.bancho).resolve()
    os.chdir(bancho)
    sys.path.insert(0, str(bancho))
    env = load_env(bancho / ".env")

    import pymysql
    import redis as redis_lib

    # use bancho.py's own calculator so recalc == live values.
    # v5.3+: app/services/performance.py (PerformanceService class)
    # older: app/usecases/performance.py (module-level function)
    try:
        from app.services.performance import PerformanceService, ScoreParams
        calculate_performances = PerformanceService().calculate_performances
    except ImportError:
        try:
            from app.usecases.performance import ScoreParams, calculate_performances
        except Exception as exc:
            print(f"{RED}💀 couldn't import bancho.py's performance module: {exc}{R}")
            print(f"{DIM}   (did bancho.py restructure? run me from an up-to-date OPS install){R}")
            return 1
    except Exception as exc:
        print(f"{RED}💀 couldn't set up bancho.py's performance service: {exc}{R}")
        return 1

    try:
        import ops_custom_pp
        modify_pp = getattr(ops_custom_pp, "modify_pp", lambda pp, ctx: pp)
    except Exception:
        modify_pp = lambda pp, ctx: pp  # noqa: E731
        print(f"{YLW}⚠️  no ops_custom_pp.py found — using pure bancho pp{R}")

    conn = pymysql.connect(
        host=env.get("DB_HOST", "127.0.0.1"), port=int(env.get("DB_PORT", "3306")),
        user=env["DB_USER"], password=env["DB_PASS"], database=env["DB_NAME"],
        charset="utf8mb4", autocommit=False,
    )
    cur = conn.cursor()

    data_dir = Path(env.get("DATA_DIRECTORY", str(bancho / ".data")))
    osu_dir = data_dir / "osu"
    osu_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{MAG}{B}🧮 OPS pp recalc — let's cook{R}\n")

    # ── phase 1: recalc every score ─────────────────────────────────────────
    mode_filter = "AND s.mode = %s" if args.mode is not None else ""
    query = (
        "SELECT s.id, s.mode, s.mods, s.max_combo, s.acc,"
        "       s.n300, s.n100, s.n50, s.nmiss, s.ngeki, s.nkatu,"
        "       s.userid, m.id AS map_id "
        "FROM scores s JOIN maps m ON s.map_md5 = m.md5 "
        f"WHERE s.status > 0 AND m.status IN ({RANKED}, {APPROVED}) {mode_filter} "
        "ORDER BY m.id"
    )
    cur.execute(query, (args.mode,) if args.mode is not None else ())
    rows = cur.fetchall()
    total = len(rows)
    print(f"  {CYN}🔹 {total} scores to recalculate{R}")

    # group by map so each .osu file is loaded once
    by_map: dict[int, list] = defaultdict(list)
    for row in rows:
        by_map[row[12]].append(row)

    updates: list[tuple[float, int]] = []
    done = missing_maps = errors = 0
    for map_id, scores in by_map.items():
        osu_path = ensure_osu_file(osu_dir, map_id)
        if osu_path is None:
            missing_maps += 1
            done += len(scores)
            continue

        params, meta = [], []
        for (sid, mode, mods, combo, acc, n300, n100, n50, nmiss, ngeki, nkatu,
             userid, _mid) in scores:
            vanilla = mode % 4 if mode != 8 else 0
            # NOTE: no "acc" here — bancho.py rejects params that set both
            # accuracy and hit counts, and hit counts are the precise ones.
            params.append(build_score_params(ScoreParams, {
                "mode": vanilla, "mods": mods, "combo": combo,
                "n300": n300, "n100": n100, "n50": n50,
                "nmiss": nmiss, "n_misses": nmiss,  # name differs across versions
                "ngeki": ngeki, "nkatu": nkatu,
            }))
            meta.append((sid, mode, mods, acc, userid))

        try:
            results = calculate_performances(str(osu_path), params)
        except Exception:
            errors += len(scores)
            done += len(scores)
            continue

        for (sid, mode, mods, acc, userid), result in zip(meta, results):
            pp = extract_pp(result)
            if pp is None:
                errors += 1
                done += 1
                continue
            try:
                pp = float(modify_pp(pp, {
                    "source": "recalc", "score_id": sid, "user_id": userid,
                    "mode": mode, "mods": mods, "acc": acc, "map_id": map_id,
                }))
            except Exception:
                pass  # a broken profile shouldn't nuke the recalc
            if not math.isfinite(pp) or pp < 0:
                pp = 0.0
            updates.append((round(pp, 3), sid))
            done += 1

        if not args.quiet:
            print(f"\r  {bar(done, total)} {done}/{total} scores", end="", flush=True)

        if len(updates) >= 2000:
            cur.executemany("UPDATE scores SET pp = %s WHERE id = %s", updates)
            conn.commit()
            updates.clear()

    if updates:
        cur.executemany("UPDATE scores SET pp = %s WHERE id = %s", updates)
        conn.commit()
    if not args.quiet:
        print()
    print(f"  {GRN}✅ scores updated{R}"
          + (f"  {YLW}({missing_maps} maps had no .osu file, {errors} calc errors — skipped){R}"
             if (missing_maps or errors) else ""))

    # ── phase 2: rebuild per-player totals (weighted top 100, bancho style) ─
    print(f"\n  {CYN}🔹 rebuilding player totals + accuracy{R}")
    cur.execute(
        "SELECT s.userid, s.mode, s.pp, s.acc "
        "FROM scores s JOIN maps m ON s.map_md5 = m.md5 "
        f"WHERE s.status = {BEST_SCORE_STATUS} AND m.status IN ({RANKED}, {APPROVED}) "
        "AND s.pp > 0 ORDER BY s.pp DESC"
    )
    per_player: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for userid, mode, pp, acc in cur.fetchall():
        bucket = per_player[(userid, mode)]
        if len(bucket) < TOP_N:
            bucket.append((float(pp), float(acc)))

    stat_updates = []
    for (userid, mode), tops in per_player.items():
        total_pp = sum(pp * (WEIGHT ** i) for i, (pp, _) in enumerate(tops))
        weight_sum = sum(WEIGHT ** i for i in range(len(tops)))
        avg_acc = (sum(acc * (WEIGHT ** i) for i, (_, acc) in enumerate(tops)) / weight_sum
                   if weight_sum else 0.0)
        stat_updates.append((round(total_pp), round(avg_acc, 3), userid, mode))
    if args.mode is not None:
        stat_updates = [u for u in stat_updates if u[3] == args.mode]
    cur.executemany("UPDATE stats SET pp = %s, acc = %s WHERE id = %s AND mode = %s",
                    stat_updates)
    conn.commit()
    print(f"  {GRN}✅ {len(stat_updates)} player/mode stat rows rebuilt{R}")

    # ── phase 3: refresh redis leaderboards ─────────────────────────────────
    print(f"\n  {CYN}🔹 refreshing Redis leaderboards{R}")
    rds = redis_lib.Redis(
        host=env.get("REDIS_HOST", "127.0.0.1"), port=int(env.get("REDIS_PORT", "6379")),
        db=int(env.get("REDIS_DB", "0")), password=env.get("REDIS_PASS") or None,
    )
    cur.execute("SELECT id, priv, country FROM users")
    users = {uid: (priv, country) for uid, priv, country in cur.fetchall()}

    modes = {mode for (_, mode) in per_player} if args.mode is None else {args.mode}
    for mode in sorted(modes):
        rds.delete(f"bancho:leaderboard:{mode}")
        for key in rds.scan_iter(f"bancho:leaderboard:{mode}:*"):
            rds.delete(key)
    for (userid, mode), _tops in per_player.items():
        if args.mode is not None and mode != args.mode:
            continue
        priv, country = users.get(userid, (0, "xx"))
        if not priv & 1:  # restricted players stay off the boards
            continue
        cur.execute("SELECT pp FROM stats WHERE id = %s AND mode = %s", (userid, mode))
        row = cur.fetchone()
        pp = int(row[0]) if row else 0
        rds.zadd(f"bancho:leaderboard:{mode}", {str(userid): pp})
        rds.zadd(f"bancho:leaderboard:{mode}:{country}", {str(userid): pp})
    print(f"  {GRN}✅ leaderboards are fresh{R}")

    conn.close()
    print(f"\n{GRN}{B}🎉 recalc complete — rankings are now canon 😎{R}")
    print(f"{DIM}   restart the server (tmux) so online players see new totals immediately{R}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
