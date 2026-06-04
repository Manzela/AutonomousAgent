#!/usr/bin/env python3
"""Assert that a pytest --junitxml report has zero skips and required nodeids passed.

Usage:
    assert_no_skips.py <junit-path> [--require-nodeid NODEID] ...

Exit codes:
    0  -- skipped == 0 AND every --require-nodeid appears as a clean PASSED testcase
    1  -- skipped > 0  OR  a required nodeid is absent / deselected / failed / errored

PRD contracts implemented
    'skipped == 0'
        The summed <testsuite skipped="N"> attribute across every <testsuite> element
        in the XML must equal zero.  A single skipped test fails the gate.

    'individually passed, not deselected'
        Every nodeid given via --require-nodeid must:
          1. Appear in the XML (not deselected by a -k / --collect-only filter)
          2. Have status = PASSED (no <skipped>, <failure>, or <error> child element)

The parser is stdlib-only (xml.etree.ElementTree) -- no third-party deps required.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Assert zero skips + required nodeids passed in a JUnit XML report."
    )
    p.add_argument("junit_path", help="Path to the pytest --junitxml output file.")
    p.add_argument(
        "--require-nodeid",
        dest="require_nodeids",
        metavar="NODEID",
        action="append",
        default=[],
        help=(
            "A pytest nodeid that MUST appear in the report as PASSED "
            "(not deselected, not skipped, not failed). "
            "Repeatable."
        ),
    )
    return p.parse_args(argv)


def _load_xml(path: Path) -> ET.Element:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        _die(f"Failed to parse XML at {path}: {exc}")
    return tree.getroot()


def _die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _count_skipped(root: ET.Element) -> int:
    """Sum the 'skipped' attribute across all <testsuite> elements.

    Handles both:
      - A single <testsuite> root element
      - A <testsuites> root wrapping one or more <testsuite> children
    """
    total = 0
    suites: list[ET.Element] = []

    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
    elif root.tag == "testsuite":
        # Root IS the testsuite; also collect any nested suites.
        suites = list(root.iter("testsuite"))
    else:
        # Unexpected root -- try iterating for any testsuite descendants.
        suites = list(root.iter("testsuite"))

    for suite in suites:
        try:
            total += int(suite.get("skipped", "0"))
        except ValueError:
            pass  # malformed attribute; skip
    return total


def _build_nodeid_status_map(root: ET.Element) -> dict[str, str]:
    """Return {nodeid: status} for every <testcase> in the report.

    Status values:
        'passed'  -- no <skipped>, <failure>, or <error> child
        'skipped' -- has a <skipped> child
        'failed'  -- has a <failure> or <error> child
    """
    result: dict[str, str] = {}
    for tc in root.iter("testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        # Reconstruct nodeid: "classname::name" (pytest's default serialisation).
        # The classname uses dots for package separators; nodeid uses slashes for
        # the module path.  We store BOTH the dot-form and the slash-form so that
        # callers can pass either style.
        dot_id = f"{classname}::{name}" if classname else name
        slash_id = dot_id.replace(".", "/", dot_id.count(".") - dot_id.count("/"))

        if tc.find("skipped") is not None:
            status = "skipped"
        elif tc.find("failure") is not None or tc.find("error") is not None:
            status = "failed"
        else:
            status = "passed"

        result[dot_id] = status
        # Also index by slash-form (for callers using filesystem paths).
        if slash_id != dot_id:
            result[slash_id] = status
        # Additionally index by the bare name so simple IDs work.
        result[name] = status
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    path = Path(args.junit_path)

    if not path.exists():
        print(f"FAIL: JUnit XML not found: {path}", file=sys.stderr)
        return 1

    root = _load_xml(path)
    failures: list[str] = []

    # ---- Gate 1: zero skips ------------------------------------------------
    skipped = _count_skipped(root)
    if skipped != 0:
        failures.append(
            f"skipped count is {skipped} (must be 0). "
            "A test that cannot run in CI keeps its task OPEN."
        )

    # ---- Gate 2: required nodeids must be present and PASSED ---------------
    if args.require_nodeids:
        nodeid_map = _build_nodeid_status_map(root)
        for nodeid in args.require_nodeids:
            status = nodeid_map.get(nodeid)
            if status is None:
                # Try matching by suffix (test name alone).
                suffix_matches = [(k, v) for k, v in nodeid_map.items() if k.endswith(nodeid)]
                if suffix_matches:
                    # Use the first suffix match.
                    _, status = suffix_matches[0]
                else:
                    failures.append(
                        f"required nodeid {nodeid!r} is ABSENT from the report "
                        "(deselected or never collected)."
                    )
                    continue
            if status != "passed":
                failures.append(
                    f"required nodeid {nodeid!r} has status {status!r} (must be 'passed')."
                )

    # ---- Report ------------------------------------------------------------
    if failures:
        print("assert_no_skips: FAIL", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    required_msg = (
        f" + {len(args.require_nodeids)} required nodeid(s) all passed"
        if args.require_nodeids
        else ""
    )
    print(f"assert_no_skips: OK (skipped=0{required_msg})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
