#!/usr/bin/env python3
"""OPS_QuickBuild — live pp hook patcher 🩹

Appends a small wrapper to bancho.py's performance module so every pp
calculation (including live score submission) runs through the server
owner's ops_custom_pp.py profile. The wrapper fails open: if the profile is
missing or broken, stock bancho pp values are used untouched.

Handles both known bancho.py layouts:
  • v5.3+   app/services/performance.py  (PerformanceService class)
  • older   app/usecases/performance.py  (module-level calculate_performances)

Idempotent — safe to run multiple times. Exits non-zero if no known layout
is found, in which case only the offline recalculator applies custom pp.

Usage:
    patch_pp_hook.py /path/to/bancho.py
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "OPS_QuickBuild PP hook"

CLASS_HOOK = '''

# --- OPS_QuickBuild PP hook (auto-generated, safe to delete) ---
# routes every pp calculation through ops_custom_pp.modify_pp; fails open.
try:
    import inspect as _ops_inspect

    import ops_custom_pp as _ops_pp

    _ops_orig_calc = PerformanceService.calculate_performances

    if _ops_inspect.iscoroutinefunction(_ops_orig_calc):

        async def _ops_wrapped_calc(self, *args, **kwargs):
            _results = await _ops_orig_calc(self, *args, **kwargs)
            return _ops_pp.apply_to_results(_results, args, kwargs)

    else:

        def _ops_wrapped_calc(self, *args, **kwargs):
            _results = _ops_orig_calc(self, *args, **kwargs)
            return _ops_pp.apply_to_results(_results, args, kwargs)

    _ops_wrapped_calc.__name__ = "calculate_performances"
    PerformanceService.calculate_performances = _ops_wrapped_calc
except Exception as _ops_exc:
    import logging as _ops_logging

    _ops_logging.getLogger(__name__).warning(
        "OPS_QuickBuild pp hook disabled (using stock pp): %r", _ops_exc
    )
# --- end OPS_QuickBuild PP hook ---
'''

FUNCTION_HOOK = '''

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

    bancho = Path(sys.argv[1])
    target = None
    for candidate in ("app/services/performance.py", "app/usecases/performance.py"):
        if (bancho / candidate).exists():
            target = bancho / candidate
            break
    if target is None:
        print("💀 no performance module found (looked in app/services/ and app/usecases/) — "
              "bancho.py layout changed; offline recalc still applies your custom pp")
        return 2

    source = target.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"✅ pp hook already installed in {target.name}, nothing to do")
        return 0

    if "class PerformanceService" in source and "def calculate_performances" in source:
        hook = CLASS_HOOK
    elif "def calculate_performances" in source:
        hook = FUNCTION_HOOK
    else:
        print(f"💀 calculate_performances() not found in {target} — "
              "can't hook live pp (offline recalc still applies your custom pp)")
        return 2

    target.write_text(source + hook, encoding="utf-8")
    print(f"✅ live pp hook installed into {target.relative_to(bancho)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
