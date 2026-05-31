"""Tests for SP-00e.4 test-integrity-gate (C6).

C6 anti-reward-hacking (ImpossibleBench arXiv 2510.20270 — models cheat by deleting/weakening
tests): a PR that DELETES an existing test function or NET-REMOVES assertions must carry an
(approved) `## Test Changes` block, else the gate FAILS. A net-new test holding/raising
coverage, or a deliberate change WITH the block, PASSES.

Pure diff-parsing + block-detection functions are unit-tested directly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

import test_integrity_gate as tig  # noqa: E402

DELETE_DIFF = """diff --git a/tests/unit/test_x.py b/tests/unit/test_x.py
--- a/tests/unit/test_x.py
+++ b/tests/unit/test_x.py
@@ -1,8 +1,3 @@
 def test_keeps():
     assert 1 == 1
-
-def test_deleted_one():
-    assert foo() == 42
-
-async def test_deleted_two():
-    assert await bar()
"""

RENAME_DIFF = """diff --git a/tests/unit/test_x.py b/tests/unit/test_x.py
@@ -1,3 +1,3 @@
-def test_renamed():
+def test_renamed():
     assert 1 == 1
"""

RELAX_DIFF = """diff --git a/tests/unit/test_y.py b/tests/unit/test_y.py
@@ -1,5 +1,3 @@
 def test_thing():
-    assert result.status == 200
-    assert result.body == "ok"
+    assert result.status == 200
"""


def test_find_deleted_test_funcs():
    assert tig.find_deleted_test_funcs(DELETE_DIFF) == [
        "test_deleted_one",
        "test_deleted_two",
    ]


def test_find_deleted_ignores_renames_kept_in_diff():
    # a function present on both - and + sides is not a net deletion
    assert tig.find_deleted_test_funcs(RENAME_DIFF) == []


def test_count_net_removed_asserts():
    # DELETE_DIFF removes 2 asserts (in deleted fns), adds 0
    assert tig.count_net_removed_asserts(DELETE_DIFF) == 2
    # RELAX_DIFF removes 2 asserts, adds 1 -> net 1
    assert tig.count_net_removed_asserts(RELAX_DIFF) == 1


def test_has_test_changes_block_none_is_false():
    assert tig.has_test_changes_block("## Test Changes\nNone\n") is False


def test_has_test_changes_block_absent_is_false():
    assert tig.has_test_changes_block("## Summary\nx\n") is False


def test_has_test_changes_block_present_is_true():
    body = "## Test Changes (C6)\nDeleted test_deleted_one — superseded by test_keeps.\n"
    assert tig.has_test_changes_block(body) is True


def test_evaluate_deletion_without_block_fails():
    ok, reasons = tig.evaluate(DELETE_DIFF, "## Summary\nno block\n")
    assert ok is False
    assert any("test_deleted_one" in r for r in reasons)


def test_evaluate_deletion_with_block_passes():
    body = "## Test Changes\nDeleted two tests, replaced by a parametrized one.\n"
    ok, _ = tig.evaluate(DELETE_DIFF, body)
    assert ok is True


def test_evaluate_net_new_tests_passes():
    add_diff = (
        "diff --git a/tests/unit/test_z.py b/tests/unit/test_z.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+def test_new():\n"
        "+    assert thing() is True\n"
    )
    ok, _ = tig.evaluate(add_diff, "## Summary\nnet-new test\n")
    assert ok is True
