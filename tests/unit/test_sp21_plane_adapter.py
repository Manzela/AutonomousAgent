"""SP-21 hermetic unit tests for the Plane CE board adapter and webhook normaliser.

All tests are hermetic — no network, no Docker, no real Plane CE instance.
REST calls are intercepted via a pytest-mock MagicMock injected as the httpx.Client.

Oracles:
  1.  PlaneBoard is a subclass of AbstractBoard.
  2.  create_card() → POST /api/v1/workspaces/.../projects/.../issues/
  3.  set_status() → PATCH .../issues/{plane_id}/ (updates existing issue)
  4.  project_plan() → 1 parent POST + N child POSTs (child has parent= set)
  5.  add_comment(origin="agent") embeds origin tag in comment body.
  6.  add_comment(origin="agent") → normalise_plane_webhook drops it (origin="agent" → bus.put=False).
  7.  normalise_plane_webhook with human comment → bus.put returns True (event accepted).
  8.  Same webhook payload twice → second call returns False (SteeringEventBus dedup).
  9.  list_blocked_cards() returns only cards with status="blocked".
  10. get_card() returns the correct card; missing card raises BoardError.
  11. list_cards(thread_id=...) returns only cards for that thread.
  12. C14: set_status(card_id, "done") raises BoardError (inherited invariant).
  13. C14: mark_done(card_id, gate=AlwaysReadyGate()) succeeds.
  14. Origin tag in add_comment body is valid JSON parseable by the normaliser.
  15. normalise_plane_webhook with unknown event type → returns None.
  16. add_comment on card with no Plane backing raises BoardError.
  17. C9-P1: invalid workspace_slug / project_id raises ValueError (path-traversal guard).
  18. C9-P1: get_comments returns list when Plane response is bare list (no AttributeError).
  19. C9-P2: failed REST create rolls back _store (no orphan card).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.adapters.inmemory.board import AlwaysReadyGate
from app.adapters.plane.board import PlaneBoard, _ORIGIN_PREFIX
from app.adapters.plane.webhook import _extract_origin, normalise_plane_webhook
from app.core.board import AbstractBoard, BoardError, project_plan
from app.core.steering import SteeringEventBus

# ── fixtures ──────────────────────────────────────────────────────────────────

WORKSPACE = "my-workspace"
PROJECT_ID = "proj-uuid-1"
THREAD = "thread-abc"


def _issue_response(plane_id: str = "plane-issue-1") -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"id": plane_id}
    resp.raise_for_status.return_value = None
    return resp


def _comment_response(comment_id: str = "cmt-1") -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"id": comment_id}
    resp.raise_for_status.return_value = None
    return resp


@pytest.fixture()
def mock_client() -> MagicMock:
    client = MagicMock()
    # default: POST issues returns a new issue
    client.post.return_value = _issue_response("plane-issue-1")
    client.patch.return_value = _issue_response("plane-issue-1")
    client.get.return_value = _issue_response("plane-issue-1")
    return client


@pytest.fixture()
def board(mock_client: MagicMock) -> PlaneBoard:
    return PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)


def _make_board_with_card(
    mock_client: MagicMock, *, plane_id: str = "plane-issue-1"
) -> tuple[PlaneBoard, Any]:
    """Helper: board with one card already pushed to Plane."""
    mock_client.post.return_value = _issue_response(plane_id)
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="task A", thread_id=THREAD)
    return b, card


# ── Oracle 1 — PlaneBoard is a subclass of AbstractBoard ─────────────────────


def test_planeboard_is_abstract_board_subclass(board: PlaneBoard) -> None:
    assert isinstance(board, AbstractBoard)


# ── Oracle 2 — create_card() → POST .../issues/ ───────────────────────────────


def test_create_card_calls_plane_post(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-99")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="my task", thread_id=THREAD)
    mock_client.post.assert_called_once()
    url_arg = mock_client.post.call_args[0][0]
    assert f"/workspaces/{WORKSPACE}/projects/{PROJECT_ID}/issues/" in url_arg
    assert card.title == "my task"
    assert card.thread_id == THREAD


# ── Oracle 3 — set_status() → PATCH .../issues/{plane_id}/ ──────────────────


def test_set_status_calls_plane_patch(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-42")
    mock_client.patch.return_value = _issue_response("plane-42")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="to update", thread_id=THREAD)
    mock_client.post.reset_mock()

    updated = b.set_status(card.id, "running")
    mock_client.patch.assert_called_once()
    patch_url = mock_client.patch.call_args[0][0]
    assert "plane-42" in patch_url
    assert updated.status == "running"


# ── Oracle 4 — project_plan() → parent POST + child POSTs ────────────────────


def test_project_plan_creates_parent_and_children(mock_client: MagicMock) -> None:
    counter = {"n": 0}

    def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        counter["n"] += 1
        return _issue_response(f"plane-{counter['n']}")

    mock_client.post.side_effect = side_effect
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    plan = {"nodes": [{"id": "n1", "summary": "step one"}, {"id": "n2", "summary": "step two"}]}
    proj = project_plan(b, plan, thread_id=THREAD, goal_title="my goal")

    # 1 parent + 2 children = 3 POST calls
    assert mock_client.post.call_count == 3
    assert proj.parent_id is not None
    assert len(proj.node_cards) == 2

    # child cards have parent_id set
    for node_id, card_id in proj.node_cards.items():
        child = b.get_card(card_id)
        assert child.parent_id == proj.parent_id, f"child {node_id} missing parent_id"


# ── Oracle 5 — add_comment(origin="agent") embeds origin tag ─────────────────


def test_add_comment_agent_embeds_origin_tag(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-1")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    b.create_card(title="x", thread_id=THREAD)
    card_id = list(b._store.keys())[0]

    # second POST call is for the comment
    mock_client.post.return_value = _comment_response("cmt-77")
    b.add_comment(card_id, "agent output here", origin="agent")

    comment_call = mock_client.post.call_args
    body_sent = comment_call[1].get("json", {}).get("comment_html", "")
    assert _ORIGIN_PREFIX in body_sent
    assert '"origin": "agent"' in body_sent


# ── Oracle 6 — add_comment agent → bus.put drops it ─────────────────────────


def test_add_comment_agent_dropped_by_bus(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-1")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    b.create_card(title="x", thread_id=THREAD)
    card_id = list(b._store.keys())[0]
    mock_client.post.return_value = _comment_response("cmt-88")

    b.add_comment(card_id, "note from agent", origin="agent")
    # Extract the comment body that was sent to Plane
    body_sent = mock_client.post.call_args[1]["json"]["comment_html"]

    # Simulate the webhook coming back with that body (Plane echoes the comment)
    bus = SteeringEventBus()
    webhook = {
        "event": "comment.created",
        "data": {
            "id": "cmt-88",
            "comment_html": body_sent,
            "issue": "plane-1",
            "created_at": "2026-06-04T00:00:00Z",
        },
    }
    result = normalise_plane_webhook(webhook, bus=bus, thread_id=THREAD)
    assert result is None, "agent-authored comment must NOT become a SteeringEvent"


# ── Oracle 7 — human comment → bus.put returns True ─────────────────────────


def test_human_comment_accepted_by_bus() -> None:
    bus = SteeringEventBus()
    webhook = {
        "event": "comment.created",
        "data": {
            "id": "cmt-human-1",
            "comment_html": "please approve",
            "issue": "plane-issue-1",
            "created_at": "2026-06-04T00:00:00Z",
        },
    }
    event = normalise_plane_webhook(webhook, bus=bus, thread_id=THREAD)
    assert event is not None, "human comment must become a SteeringEvent"
    assert event["channel"] == "board"
    assert event["origin_id"] == "cmt-human-1"
    assert event["thread_id"] == THREAD


# ── Oracle 8 — same webhook twice → second dropped (bus dedup) ───────────────


def test_duplicate_webhook_deduped() -> None:
    bus = SteeringEventBus()
    payload = {
        "event": "comment.created",
        "data": {
            "id": "cmt-dup",
            "comment_html": "approve please",
            "issue": "plane-issue-2",
            "created_at": "2026-06-04T00:01:00Z",
        },
    }
    first = normalise_plane_webhook(payload, bus=bus, thread_id=THREAD)
    second = normalise_plane_webhook(payload, bus=bus, thread_id=THREAD)
    assert first is not None, "first webhook must be accepted"
    assert second is None, "duplicate webhook must be dropped by bus dedup"


# ── Oracle 9 — list_blocked_cards() returns only blocked cards ───────────────


def test_list_blocked_cards_filter(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-1")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    c_blocked = b.create_card(title="waiting", thread_id=THREAD, status="blocked")
    mock_client.post.return_value = _issue_response("plane-2")
    _c_running = b.create_card(title="active", thread_id=THREAD, status="running")

    blocked = b.list_blocked_cards()
    assert len(blocked) == 1
    assert blocked[0].id == c_blocked.id

    # thread_id filter works too
    blocked_thread = b.list_blocked_cards(thread_id=THREAD)
    assert len(blocked_thread) == 1

    blocked_other = b.list_blocked_cards(thread_id="other-thread")
    assert blocked_other == []


# ── Oracle 10 — get_card() and missing-card error ────────────────────────────


def test_get_card_returns_correct_card(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-3")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="alpha", thread_id=THREAD)
    fetched = b.get_card(card.id)
    assert fetched.id == card.id
    assert fetched.title == "alpha"


def test_get_card_missing_raises_board_error(board: PlaneBoard) -> None:
    with pytest.raises(BoardError, match="no such card"):
        board.get_card("nonexistent-id")


# ── Oracle 11 — list_cards(thread_id=...) scoped correctly ──────────────────


def test_list_cards_scoped_by_thread(mock_client: MagicMock) -> None:
    ids = iter(["p1", "p2", "p3"])
    mock_client.post.side_effect = lambda *a, **kw: _issue_response(next(ids))
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    c1 = b.create_card(title="t1", thread_id="thread-A")
    c2 = b.create_card(title="t2", thread_id="thread-A")
    _c3 = b.create_card(title="t3", thread_id="thread-B")

    a_cards = b.list_cards(thread_id="thread-A")
    assert {c.id for c in a_cards} == {c1.id, c2.id}

    b_cards = b.list_cards(thread_id="thread-B")
    assert len(b_cards) == 1

    all_cards = b.list_cards()
    assert len(all_cards) == 3


# ── Oracle 12 — C14: set_status "done" raises BoardError ─────────────────────


def test_c14_set_status_done_raises(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-x")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="blocked by C14", thread_id=THREAD)
    with pytest.raises(BoardError, match="gate-derived"):
        b.set_status(card.id, "done")


# ── Oracle 13 — C14: mark_done with AlwaysReadyGate succeeds ────────────────


def test_c14_mark_done_with_always_ready_gate(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-y")
    mock_client.patch.return_value = _issue_response("plane-y")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="ready to close", thread_id=THREAD)
    done_card = b.mark_done(card.id, gate=AlwaysReadyGate())
    assert done_card.status == "done"


# ── Oracle 14 — origin tag JSON is parseable ─────────────────────────────────


def test_origin_tag_is_valid_json(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-1")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    b.create_card(title="x", thread_id=THREAD)
    card_id = list(b._store.keys())[0]
    mock_client.post.return_value = _comment_response("cmt-z")
    b.add_comment(card_id, "body text", origin="agent")

    body_sent = mock_client.post.call_args[1]["json"]["comment_html"]
    # _extract_origin must round-trip correctly
    assert _extract_origin(body_sent) == "agent"
    assert _extract_origin("no tag here") == "human"
    assert _extract_origin("") == "human"


# ── Oracle 15 — unknown event type → None ────────────────────────────────────


def test_unknown_event_type_returns_none() -> None:
    bus = SteeringEventBus()
    for event_type in ("issue.created", "issue.updated", "member.added", ""):
        result = normalise_plane_webhook(
            {"event": event_type, "data": {}},
            bus=bus,
            thread_id=THREAD,
        )
        assert result is None, f"event type {event_type!r} must return None"


# ── Oracle 16 — add_comment on un-pushed card raises BoardError ──────────────


def test_add_comment_unpushed_card_raises(board: PlaneBoard) -> None:
    from app.core.board import Card

    orphan = Card(id="orphan-2", title="x", thread_id=THREAD)
    # Inject directly into _store without going through _put (no Plane create)
    board._store["orphan-2"] = orphan
    # _plane_ids has no entry — add_comment must raise
    with pytest.raises(BoardError, match="no Plane issue"):
        board.add_comment("orphan-2", "hello", origin="human")


# ── Oracle 17 — C9-P1: slug validation blocks path traversal ─────────────────


@pytest.mark.parametrize("slug", ["../../admin", "ws/evil", "ws%2Fevil", "a" * 257, ""])
def test_invalid_workspace_slug_raises(slug: str) -> None:
    with pytest.raises(ValueError, match="workspace_slug"):
        PlaneBoard(slug, PROJECT_ID)


@pytest.mark.parametrize("pid", ["../../admin", "proj/evil", "proj%2Fevil", "p" * 257, ""])
def test_invalid_project_id_raises(pid: str) -> None:
    with pytest.raises(ValueError, match="project_id"):
        PlaneBoard(WORKSPACE, pid)


def test_valid_slugs_accepted() -> None:
    mock = MagicMock()
    mock.post.return_value = _issue_response()
    # UUIDs, slugs with hyphens/underscores/dots must all work
    PlaneBoard("my-workspace", "proj-uuid-1234", client=mock)
    PlaneBoard("My_Workspace.v2", "abc123", client=mock)


# ── Oracle 18 — C9-P1: get_comments on bare-list response ───────────────────


def test_get_comments_bare_list_response(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-gc")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="x", thread_id=THREAD)

    # Plane returns a bare list (no "results" envelope)
    list_resp = MagicMock()
    list_resp.json.return_value = [{"id": "cmt-a"}, {"id": "cmt-b"}]
    list_resp.raise_for_status.return_value = None
    mock_client.get.return_value = list_resp

    comments = b.get_comments(card.id)
    assert comments == [{"id": "cmt-a"}, {"id": "cmt-b"}]


def test_get_comments_results_envelope(mock_client: MagicMock) -> None:
    mock_client.post.return_value = _issue_response("plane-gc2")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)
    card = b.create_card(title="x", thread_id=THREAD)

    envelope_resp = MagicMock()
    envelope_resp.json.return_value = {"results": [{"id": "cmt-1"}], "count": 1}
    envelope_resp.raise_for_status.return_value = None
    mock_client.get.return_value = envelope_resp

    comments = b.get_comments(card.id)
    assert comments == [{"id": "cmt-1"}]


# ── Oracle 19 — C9-P2: failed REST create rolls back _store ──────────────────


def test_create_card_rest_failure_rolls_back(mock_client: MagicMock) -> None:
    mock_client.post.side_effect = Exception("Plane 500 error")
    b = PlaneBoard(WORKSPACE, PROJECT_ID, client=mock_client)

    with pytest.raises(Exception, match="Plane 500"):
        b.create_card(title="doomed", thread_id=THREAD)

    # Card must NOT appear in the store (no orphan zombie)
    assert b.list_cards() == [], "failed create must not leave a card in _store"
