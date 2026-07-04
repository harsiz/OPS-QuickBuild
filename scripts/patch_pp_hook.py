#!/usr/bin/env python3
"""OPS_QuickBuild — live pp hook patcher 🩹

Appends a small wrapper to bancho.py's app/usecases/performance.py so every
pp calculation (including live score submission) runs through the server
owner's ops_custom_pp.py profile. The wrapper fails open: if the profile is
missing or broken, stock bancho pp values are used untouched.

Idempotent — safe to run multiple times. Exits non-zero if the expected
function isn't found (bancho.py restructured), in which case only the offline
recalculator applies custom pp.

Usage:
    patch_pp_hook.py /path/to/bancho.py
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "OPS_QuickBuild PP hook"

HOOK = '''

# --- OPS_QuickBuild PP hook (auto-generated, safe to delete) ---
# routes every pp calculation through ops_custom_pp.modify_pp; fails open.
try:
    import inspect as _ops_inspect

    import ops_custom_pp as _ops_pp

    _ops_orig_calculate_performances = calculate_performances

    if _ops_inspect.iscoroutinefunction(_ops_orig_calculate_performances):

        async def calculate_performances(*args, **kwargs):  # type: ignore[no-redef]
            _results = await _ops_orig_calculate_performances(*args, **kwargs)
            return _ops_pp.apply_to_results(_results, args, kwargs)

    else:

        def calculate_performances(*args, **kwargs):  # type: ignore[no-redef]
            _results = _ops_orig_calculate_performances(*args, **kwargs)
            return _ops_pp.apply_to_results(_results, args, kwargs)

except Exception as _ops_exc:
    import logging as _ops_logging

    _ops_logging.getLogger(__name__).warning(
        "OPS_QuickBuild pp hook disabled (using stock pp): %r", _ops_exc
    )
# --- end OPS_QuickBuild PP hook ---
'''


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_pp_hook.py /path/to/bancho.py")
        return 1

    target = Path(sys.argv[1]) / "app" / "usecases" / "performance.py"
    if not target.exists():
        print(f"💀 {target} not found — bancho.py layout changed?")
        return 2

    source = target.read_text(encoding="utf-8")
    if MARKER in source:
        print("✅ pp hook already installed, nothing to do")
        return 0
    if "def calculate_performances" not in source:
        print("💀 calculate_performances() not found in performance.py — "
              "can't hook live pp (offline recalc still applies your custom pp)")
        return 2

    target.write_text(source + HOOK, encoding="utf-8")
    print("✅ live pp hook installed into app/usecases/performance.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
