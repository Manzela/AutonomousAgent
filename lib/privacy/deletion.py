"""GDPR Article 17 right-to-erasure helper.

Purges every data store of a single user_id. Idempotent and audit-logged.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def delete_user_data(user_id: str) -> dict[str, Any]:
    """Delete every artifact tied to user_id across Honcho, Chroma, LiteLLM, MALT."""
    if not user_id or "/" in user_id or ".." in user_id:
        raise ValueError("invalid user_id (path-traversal guard)")

    summary: dict[str, Any] = {"user_id": user_id, "deletions": {}}

    # 1. Honcho (Postgres)
    from lib.clients.honcho import HonchoClient

    summary["deletions"]["honcho"] = HonchoClient().delete_user(user_id)

    # 2. Chroma collections
    from lib.clients.chroma import ChromaClient

    summary["deletions"]["chroma"] = ChromaClient().delete_user(user_id)

    # 3. LiteLLM SpendLogs
    from lib.clients.litellm_db import LiteLLMDb

    summary["deletions"]["litellm"] = LiteLLMDb().delete_spend_logs(user_id)

    # 4. MALT blobs (GCS)
    from lib.trajectory.shipper import TrajectoryShipper

    summary["deletions"]["malt"] = TrajectoryShipper.delete_user_blobs(user_id)

    logger.info("gdpr.delete_user_data complete", extra=summary)
    return summary
