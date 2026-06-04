from __future__ import annotations
import os
import httpx
import logging
from typing import Any

logger = logging.getLogger(__name__)


class HonchoClient:
    def __init__(self) -> None:
        pass

    def delete_user(self, user_id: str) -> dict[str, Any]:
        """GDPR deletion for Honcho user/peer data."""
        api_key = os.environ.get("HONCHO_API_KEY")
        base_url = os.environ.get("HONCHO_BASE_URL", "https://api.honcho.dev").rstrip("/")

        if not api_key:
            return {
                "status": "skipped",
                "reason": "HONCHO_API_KEY not configured",
                "user_id": user_id,
            }

        summary: dict[str, Any] = {
            "status": "completed",
            "user_id": user_id,
            "deleted_resources": [],
            "errors": [],
        }

        # 1. Try SDK if installed
        try:
            from honcho import Honcho

            # Honcho SDK delete usage
            client = Honcho(api_key=api_key, base_url=base_url)
            # Delete user session and peers
            try:
                peer = client.peer(user_id)
                if hasattr(peer, "delete") and callable(peer.delete):
                    peer.delete()
                    summary["deleted_resources"].append("peer")
            except Exception as e:
                logger.debug("Honcho SDK peer delete failed, trying fallback: %s", e)
        except ImportError:
            pass

        # 2. Direct HTTP REST calls to delete the peer and session data
        # We try to delete the peer resource: DELETE {base_url}/v2/peers/{user_id}
        # and DELETE {base_url}/v3/peers/{user_id}
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            with httpx.Client(headers=headers, timeout=10.0) as client:
                # Try v2 first
                resp = client.delete(f"{base_url}/v2/peers/{user_id}")
                if resp.status_code in (200, 204):
                    summary["deleted_resources"].append(f"v2/peers/{user_id}")
                elif resp.status_code == 404:
                    summary["deleted_resources"].append(f"v2/peers/{user_id} (not_found)")
                else:
                    # Try v3
                    resp_v3 = client.delete(f"{base_url}/v3/peers/{user_id}")
                    if resp_v3.status_code in (200, 204):
                        summary["deleted_resources"].append(f"v3/peers/{user_id}")
                    elif resp_v3.status_code == 404:
                        summary["deleted_resources"].append(f"v3/peers/{user_id} (not_found)")
                    else:
                        raise Exception(
                            f"Delete peer failed. v2: {resp.status_code}, v3: {resp_v3.status_code}"
                        )
        except Exception as e:
            logger.error("Honcho delete_user HTTP request failed: %s", e)
            summary["errors"].append(str(e))
            if not summary["deleted_resources"]:
                summary["status"] = "failed"

        return summary
