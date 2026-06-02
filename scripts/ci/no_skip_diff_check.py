#!/usr/bin/env python3
"""C6 no-skip gate — detect newly-added skip markers AND bare runtime skips.

Backs the "No new skip/skipif/xfail/manual markers (C6)" workflow
(.github/workflows/no-skip-on-remediation.yml). Reads a unified diff on stdin
(``git diff BASE...HEAD``) and exits non-zero if any ADDED line introduces a
forbidden skip.

C6 contract: "a test that can't run in CI keeps its task OPEN." A skip removes a
test from the run, so newly-added skips are blocked. The detection is
forward-looking — only ADDED lines (``+`` prefix, excluding the ``+++`` file
header) are inspected, so the pre-existing markers are unaffected.

Forms blocked (OD-5 adds the bare runtime call, which the marker-only regex
previously MISSED):

    pytest.mark.skip / pytest.mark.skipif / pytest.mark.xfail   (markers)
    pytest.mark.manual / @manual                               (forbidden tag)
    pytest.skip( / pytest.xfail(                                (bare RUNTIME calls)

STDLIB ONLY — no third-party imports.
"""

from __future__ import annotations

import re
import sys

# Single-source the detection pattern. Mirrors the historical workflow regex
# (pytest.mark.{skip,skipif,xfail} | @manual | pytest.mark.manual) and ADDS the
# bare runtime calls pytest.skip( / pytest.xfail( that the marker-only form
# missed (OD-5). ``pytest\.(skip|xfail)\(`` matches the bare call; the
# ``\.mark\.`` segment keeps the marker forms distinct.
FORBIDDEN_PATTERN = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)"
    r"|@manual"
    r"|pytest\.mark\.manual"
    r"|pytest\.(skip|xfail)\("
)


def find_forbidden_added_lines(diff_text: str) -> list[str]:
    """Return the ADDED diff lines that introduce a forbidden skip.

    Only lines starting with a single ``+`` (added content) are inspected.
    The unified-diff file header ``+++ b/...`` (which starts with ``++``) and
    context/removed lines are ignored.
    """
    hits: list[str] = []
    for raw in diff_text.splitlines():
        # Added content lines start with exactly one '+'. The '+++ ' file
        # header starts with '++' and must be excluded.
        if not raw.startswith("+") or raw.startswith("++"):
            continue
        content = raw[1:]
        if FORBIDDEN_PATTERN.search(content):
            hits.append(content)
    return hits


def main(argv: list[str] | None = None) -> int:
    diff_text = sys.stdin.read()
    hits = find_forbidden_added_lines(diff_text)
    if hits:
        print(
            "::error::PR adds skip/skipif/xfail/manual markers or bare "
            "pytest.skip()/pytest.xfail() calls (C6 forbids new skips):"
        )
        for line in hits:
            print(line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
