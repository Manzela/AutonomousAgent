from __future__ import annotations
import os
import httpx
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ChromaClient:
    def __init__(self) -> None:
        pass

    def delete_user(self, user_id: str) -> dict[str, Any]:
        """GDPR deletion for Chroma collections."""
        host = os.environ.get("CHROMA_CLOUD_HOST") or "api.trychroma.com"
        port = os.environ.get("CHROMA_CLOUD_PORT") or "443"
        api_key = os.environ.get("CHROMA_CLOUD_API_KEY")
        tenant = os.environ.get("CHROMA_CLOUD_TENANT") or "default_tenant"
        database = os.environ.get("CHROMA_CLOUD_DATABASE") or "default_database"

        # Construct base URL
        if not host.startswith(("http://", "https://")):
            scheme = "https" if port == "443" or "ssl" in host else "http"
            base_url = f"{scheme}://{host}:{port}"
        else:
            base_url = host

        headers = {}
        if api_key:
            headers["x-chroma-token"] = api_key

        summary: dict[str, Any] = {
            "status": "completed",
            "collections_processed": [],
            "deleted_count": 0,
            "errors": [],
        }

        # Step 1: List collections
        collections = []
        try:
            with httpx.Client(headers=headers, timeout=10.0) as client:
                params = {"tenant": tenant, "database": database}
                response = client.get(f"{base_url}/api/v1/collections", params=params)
                if response.status_code == 200:
                    collections = response.json()
                else:
                    response_v2 = client.get(
                        f"{base_url}/api/v2/tenants/{tenant}/databases/{database}/collections"
                    )
                    if response_v2.status_code == 200:
                        collections = response_v2.json()
                    else:
                        raise Exception(
                            f"Failed to fetch collections. Status: {response.status_code}"
                        )
        except Exception as e:
            logger.error("Chroma delete_user list collections failed: %s", e)
            summary["status"] = "failed"
            summary["errors"].append(str(e))
            return summary

        # Step 2: Delete documents matching user_id from each collection
        for col in collections:
            col_id = col.get("id")
            col_name = col.get("name")
            if not col_id:
                continue

            try:
                with httpx.Client(headers=headers, timeout=10.0) as client:
                    del_url = f"{base_url}/api/v1/collections/{col_id}/delete"
                    payload = {"where": {"user_id": user_id}}
                    resp = client.post(del_url, json=payload)
                    if resp.status_code == 200:
                        deleted_ids = resp.json()
                        count = len(deleted_ids) if isinstance(deleted_ids, list) else 0
                        summary["collections_processed"].append(
                            {"name": col_name, "id": col_id, "status": "success", "count": count}
                        )
                        summary["deleted_count"] += count
                    else:
                        payload_v2 = {"where": {"user_id": {"$eq": user_id}}}
                        resp_v2 = client.post(del_url, json=payload_v2)
                        if resp_v2.status_code == 200:
                            deleted_ids = resp_v2.json()
                            count = len(deleted_ids) if isinstance(deleted_ids, list) else 0
                            summary["collections_processed"].append(
                                {
                                    "name": col_name,
                                    "id": col_id,
                                    "status": "success",
                                    "count": count,
                                }
                            )
                            summary["deleted_count"] += count
                        else:
                            raise Exception(f"Delete failed with status: {resp.status_code}")
            except Exception as e:
                logger.error(
                    "Chroma delete from collection %s (%s) failed: %s", col_name, col_id, e
                )
                summary["collections_processed"].append(
                    {"name": col_name, "id": col_id, "status": "error", "reason": str(e)}
                )
                summary["errors"].append(f"col:{col_name} error: {e}")

        return summary
