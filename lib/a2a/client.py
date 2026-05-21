"""A2A sender — outbound JSON-RPC client.

Day 1 stub. Day 3 implements:
  - async def send_message(peer_url, message, agent_identity) -> Task
  - async def get_task(peer_url, task_id) -> Task
  - async def cancel_task(peer_url, task_id) -> Task
  - A2AError hierarchy mapping JSON-RPC error codes (-32xxx) to Python exceptions
  - httpx.AsyncClient with retry + timeout policy

Day 4 adds async def stream_message(...) using httpx-sse. Day 5 wires
mint_token() before every outbound call. Day 6 adds OTel span + traceparent
header injection.

Spec reference: docs/specification.md §6.4 (request envelope), §7.6.1 (message/send).
TODO(Day 3): implement send_message + A2AError hierarchy.
"""

from __future__ import annotations

# Intentionally empty until Day 3.
