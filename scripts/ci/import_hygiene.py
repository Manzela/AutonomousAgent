#!/usr/bin/env python3
"""SP-00e.3 — import-hygiene gate (Executor Contract C5).

After a clean-container `uv sync --frozen`, import EVERY first-party module under the
given roots (default: app, lib). Any **ModuleNotFoundError** fails the PR — that is the
framework-cosplay / deleted-dep / garak-removal-breaks-import signal. Other import-time
exceptions (RuntimeError needing a live service, a non-MNFE ImportError, etc.) are
env-dependent and reported as WARN, never fail (faithful to the PRD: "any
ModuleNotFoundError fails").

Baseline (verified 2026-05-31): app/ + lib/ = 111 modules, 0 ModuleNotFoundError — so the
gate is green today and goes red only on a NEW broken import; no baseline-allowlist needed.

Usage:
    import_hygiene.py [--roots app,lib] [--base <repo-root>] [--list]
    --list  : discover + print module names only (no import; stdlib-only)

Exit 0 iff no module raised ModuleNotFoundError.
"""

from __future__ import annotations

import argparse
import importlib
import pathlib
import subprocess
import sys


def discover_modules(roots: list[str], base: str) -> list[str]:
    """Walk each root under `base`; return sorted unique dotted module names.

    `app/core/orchestrator.py` -> `app.core.orchestrator`; `app/core/__init__.py` ->
    `app.core`. Skips __pycache__.
    """
    base_path = pathlib.Path(base)
    mods: set[str] = set()
    for root in roots:
        for py in (base_path / root).rglob("*.py"):
            rel = py.relative_to(base_path)
            if "__pycache__" in rel.parts or _is_test_path(rel):
                continue
            parts = list(rel.with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                mods.add(".".join(parts))
    return sorted(mods)


def _is_test_path(rel: pathlib.Path) -> bool:
    """Tests are NOT runtime modules: they need the dev group + pytest/conftest context, and
    importing them standalone is meaningless to C5 (which targets the runtime import path).
    Excludes any module under a `tests/` dir, or named test_*/*_test/conftest."""
    if "tests" in rel.parts:
        return True
    stem = rel.stem
    return stem.startswith("test_") or stem.endswith("_test") or stem == "conftest"


def classify_import_error(exc: BaseException) -> str:
    """'fail' for a missing module (the C5 signal), 'warn' for anything else."""
    if isinstance(exc, ModuleNotFoundError):
        return "fail"
    return "warn"


def _repo_root() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "."


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="import-hygiene gate (C5)")
    ap.add_argument("--roots", default="app,lib", help="comma-separated package roots")
    ap.add_argument("--base", default=None, help="repo root (default: git toplevel)")
    ap.add_argument("--list", action="store_true", help="discover + print modules only (no import)")
    args = ap.parse_args(argv)

    base = args.base or _repo_root()
    roots = [r.strip() for r in args.roots.split(",") if r.strip()]
    mods = discover_modules(roots, base)

    if args.list:
        for m in mods:
            print(m)
        print(f"== import-hygiene: discovered {len(mods)} module(s) under {roots} ==")
        return 0

    if base not in sys.path:
        sys.path.insert(0, base)

    ok = 0
    failed: list[tuple[str, str]] = []
    warned: list[tuple[str, str]] = []
    for m in mods:
        try:
            importlib.import_module(m)
            ok += 1
        except Exception as e:  # noqa: BLE001 — we classify, never swallow
            if classify_import_error(e) == "fail":
                failed.append((m, f"{type(e).__name__}: {e}"))
            else:
                warned.append((m, type(e).__name__))

    for m, msg in failed:
        print(f"::error::import-hygiene: {m} -> {msg}")
    for m, t in warned:
        print(f"::warning::import-hygiene (env-dependent, not failing): {m} -> {t}")
    print(
        f"== import-hygiene: {len(mods)} modules | ok={ok} "
        f"ModuleNotFoundError={len(failed)} warn={len(warned)} "
        f"=> {'FAIL' if failed else 'PASS'} =="
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
