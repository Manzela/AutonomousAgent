"""A2A receiver — FastAPI JSON-RPC dispatch.

Day 1 stub. Day 2 implements:
  - FastAPI app exposing POST /
  - JSON-RPC 2.0 envelope parsing (-32700 on malformed, -32601 on unknown method)
  - method dispatch table: message/send, message/stream, tasks/get,
    tasks/subscribe, tasks/cancel, agent/getAuthenticatedExtendedCard
  - hard-coded handle_send_message returning synthetic Task (SUBMITTED state)

Day 4 adds SSE streaming (StreamingResponse). Day 5 adds JWT verifier
middleware. Day 6 adds OTel traceparent extract + context attach. Day 7
swaps the hard-coded Task for task_bridge.bridge_inbound_to_taskspec.

Spec reference: docs/specification.md §6 (JSON-RPC + SSE), §7.6 (RPC methods).
TODO(Day 2): implement FastAPI app + dispatch table.
"""

from __future__ import annotations

# Intentionally empty until Day 2. Importing this module today is safe
# (no side effects) but provides no functionality — the plugin's register()
# does not import this file in the Day-1 scaffolding pass.
