"""A2A <-> Hermes TaskSpec bridge.

Day 1 stub. Day 7 implements:
  - bridge_inbound_to_taskspec(a2a_task, agent_identity) -> TaskSpec
    (creates a TaskSpec via lib.anchors when an A2A message/send arrives)
  - bridge_taskspec_status_to_a2a(spec) -> TaskState
    (maps SpecStatus enum -> A2A TaskState enum)
  - on inbound tasks/cancel: dispatch to lib.anchors /cancel slash command path

Mapping table (Day 7 will codify):
  SpecStatus.draft         -> TaskState.SUBMITTED
  SpecStatus.draft_locked  -> TaskState.WORKING
  SpecStatus.locked        -> TaskState.WORKING
  SpecStatus.superseded    -> TaskState.CANCELED
  (completion + failure semantics are TBD pending evaluator integration)

Spec reference: docs/specification.md §7.6 (task lifecycle), §9 (task states).
TODO(Day 7): implement bridge functions + mapping table + cancel dispatch.
"""

from __future__ import annotations

# Intentionally empty until Day 7.
