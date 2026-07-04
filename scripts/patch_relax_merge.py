#!/usr/bin/env python3
"""OPS_QuickBuild — relax-merge patcher 🤝

Appends a flag-controlled hook to bancho.py's gamemodes module. When the
server owner's ops_custom_pp.py sets RELAX_PLAYS_AS_VANILLA = True, relax
plays are classified as VANILLA scores at submission time (the RX mod stays
in the mods bitmask, like HD or HR), which makes relax fully native on the
main leaderboards: live stats updates, shared map leaderboards, one board.

Without the flag the hook is inert — stock behavior. Fails open either way.
Idempotent — safe to run multiple times.

NOTE: flipping the flag on an existing server also needs a one-time score
migration (mode 4-6 → 0-2 + personal-best dedup) — see the OPS docs/chat.

Usage:
    patch_relax_merge.py /path/to/bancho.py
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "OPS_QuickBuild relax-merge hook"

HOOK = '''

# --- OPS_QuickBuild relax-merge hook (auto-generated, safe to delete) ---
# when ops_custom_pp.RELAX_PLAYS_AS_VANILLA is set, relax plays are
# classified as vanilla scores (RX stays in the mods bitmask), putting
# them on the main leaderboards natively. inert without the flag.
try:
    import ops_custom_pp as _ops_pp

    if getattr(_ops_pp, "RELAX_PLAYS_AS_VANILLA", False):

        def _ops_from_params(cls, mode_vn, mods):
            mode = mode_vn
            if mods & Mods.AUTOPILOT:
                mode += 8
            return cls(mode)

        GameMode.from_params = classmethod(_ops_from_params)
except Exception as _ops_exc:
    import logging as _ops_logging

    _ops_logging.getLogger(__name__).warning(
        "OPS_QuickBuild relax-merge hook disabled: %r", _ops_exc
    )
# --- end OPS_QuickBuild relax-merge hook ---
'''


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_relax_merge.py /path/to/bancho.py")
        return 1

    target = Path(sys.argv[1]) / "app" / "constants" / "gamemodes.py"
    if not target.exists():
        print(f"💀 {target} not found — bancho.py layout changed?")
        return 2

    source = target.read_text(encoding="utf-8")
    if MARKER in source:
        print("✅ relax-merge hook already installed, nothing to do")
        return 0
    if "class GameMode" not in source or "def from_params" not in source:
        print("💀 GameMode.from_params not found — can't install relax-merge hook")
        return 2

    target.write_text(source + HOOK, encoding="utf-8")
    print("✅ relax-merge hook installed into app/constants/gamemodes.py "
          "(inert until RELAX_PLAYS_AS_VANILLA = True in ops_custom_pp.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
