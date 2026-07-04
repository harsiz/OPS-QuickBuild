"""OPS_QuickBuild — YOUR custom pp profile 🎛️

This file is installed as <bancho.py>/ops_custom_pp.py and is consulted for
every pp calculation on your server:

  • live score submission (via the OPS hook patched into bancho.py)
  • the full recalculator (recalc-pp.sh)

The default does NOTHING — you get bancho's current pp system, exactly as
osu! computes it today. Edit modify_pp() to change the math, then run:

    ./recalc-pp.sh        # rewrites pp on every existing score + rankings
    (and restart the server so live submissions use the new math)

ctx keys you can use (best effort — some may be missing on live submits):
    source   'live' or 'recalc'
    mode     server mode 0-8 (0-3 vanilla, 4-6 relax, 8 autopilot)
    mods     mod bitmask (osu!stable values: 64=DT, 16=HR, 128=RX, ...)
    acc      accuracy (0-100)
    map_id   beatmap id
    user_id / score_id   (recalc only)
"""
from __future__ import annotations

# quick lever: global multiplier applied to ALL pp. 1.0 = pure bancho.
PP_MULTIPLIER = 1.0

# handy mod bits if you want mod-based logic
MOD_HIDDEN = 8
MOD_HARDROCK = 16
MOD_DOUBLETIME = 64
MOD_RELAX = 128
MOD_FLASHLIGHT = 1024
MOD_AUTOPILOT = 8192


def modify_pp(pp: float, ctx: dict) -> float:
    """Take bancho's calculated pp, return the pp YOUR server awards.

    Default: bancho's current pp, untouched (times PP_MULTIPLIER, which
    defaults to 1.0). Some ideas:

        # flat 1.2x server-wide inflation:
        #     return pp * 1.2

        # buff relax so the rx players stop complaining:
        #     mods = ctx.get("mods", 0) or 0
        #     if mods & MOD_RELAX:
        #         return pp * 1.35

        # hard cap to keep the leaderboard sane:
        #     return min(pp, 2000.0)

        # softcap curve (diminishing returns past 727pp):
        #     cap = 727.0
        #     return pp if pp <= cap else cap + (pp - cap) ** 0.85
    """
    return pp * PP_MULTIPLIER


# ─────────────────────────────────────────────────────────────────────────────
# plumbing below — you shouldn't need to touch anything past this line.
# apply_to_results() is called by the live hook inside bancho.py and walks
# whatever result shape bancho.py returns, applying modify_pp to each score.
# it fails open: any error means stock pp values pass through untouched.
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_results(results, args=None, kwargs=None):
    try:
        score_params = _extract_score_params(args, kwargs)
        for i, result in enumerate(results or []):
            ctx = {"source": "live"}
            if score_params is not None and i < len(score_params):
                sp = score_params[i]
                ctx["mode"] = getattr(sp, "mode", None)
                ctx["mods"] = getattr(sp, "mods", None)
                ctx["acc"] = getattr(sp, "acc", None)
                ctx["combo"] = getattr(sp, "combo", None)
            _apply_one(result, ctx)
    except Exception:
        pass
    return results


def _extract_score_params(args, kwargs):
    # calculate_performances(osu_file_path, scores) — dig the ScoreParams list
    # out of the call so modify_pp gets mods/mode/acc on live submissions too.
    try:
        candidates = []
        if kwargs:
            candidates.append(kwargs.get("scores"))
        if args:
            candidates.extend(args)
        for cand in candidates:
            if cand is None or isinstance(cand, (str, bytes)):
                continue
            items = list(cand)
            if items and hasattr(items[0], "mods"):
                return items
    except Exception:
        pass
    return None


def _apply_one(result, ctx_base):
    # bancho.py v5.3+ returns frozen dataclasses: PerformanceResult(performance=
    # PerformanceRating(pp=...), ...); older versions returned nested dicts.
    # we handle both and stay defensive about the exact shape.
    perf = None
    if isinstance(result, dict):
        perf = result.get("performance", result)
    elif hasattr(result, "performance"):
        perf = result.performance

    if isinstance(perf, dict) and isinstance(perf.get("pp"), (int, float)):
        try:
            new_pp = float(modify_pp(float(perf["pp"]), dict(ctx_base)))
            if new_pp >= 0 and new_pp == new_pp:  # not NaN
                perf["pp"] = new_pp
        except Exception:
            pass
    elif perf is not None and isinstance(getattr(perf, "pp", None), (int, float)):
        try:
            new_pp = float(modify_pp(float(perf.pp), dict(ctx_base)))
            if new_pp >= 0 and new_pp == new_pp:
                # object.__setattr__ so frozen dataclasses can be updated too
                object.__setattr__(perf, "pp", new_pp)
        except Exception:
            pass
