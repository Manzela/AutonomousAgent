from __future__ import annotations
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LiteLLMDb:
    def __init__(self) -> None:
        pass

    def delete_spend_logs(self, user_id: str) -> dict[str, Any]:
        """GDPR deletion for LiteLLM SpendLogs from Postgres."""
        conn_str = os.environ.get("LITELLM_DB_URL") or os.environ.get("DATABASE_URL")
        if not conn_str:
            return {
                "status": "skipped",
                "reason": "No database connection string (LITELLM_DB_URL / DATABASE_URL unset)",
                "user_id": user_id,
            }

        summary = {"status": "completed", "deleted_count": 0, "errors": []}

        try:
            import psycopg
        except ImportError:
            summary["status"] = "failed"
            summary["errors"].append("psycopg not installed")
            return summary

        try:
            with psycopg.connect(conn_str) as conn:
                with conn.cursor() as cur:
                    # 1. Determine table name and columns
                    table_name = None
                    for candidate in ("LiteLLM_SpendLogs", "litellm_spendlogs"):
                        cur.execute(
                            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s)",
                            (candidate,),
                        )
                        if cur.fetchone()[0]:
                            table_name = candidate
                            break

                    if not table_name:
                        table_name = "LiteLLM_SpendLogs"

                    # Fetch actual columns of this table
                    cur.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                        (table_name,),
                    )
                    columns = {row[0].lower() for row in cur.fetchall()}

                    # 2. Delete rows matching user_id
                    # We check for columns: 'user', 'end_user', 'api_key', 'user_id'
                    clauses = []
                    params = []
                    for col in ("user", "end_user", "api_key", "user_id"):
                        col_exact = next((c for c in columns if c == col), None)
                        if not col_exact:
                            normalized_col = col.replace("_", "").lower()
                            col_exact = next(
                                (
                                    c
                                    for c in columns
                                    if c.lower().replace("_", "") == normalized_col
                                ),
                                None,
                            )

                        if col_exact:
                            clauses.append(f'"{col_exact}" = %s')
                            params.append(user_id)

                    if clauses:
                        query = f'DELETE FROM "{table_name}" WHERE ' + " OR ".join(clauses)
                        cur.execute(query, tuple(params))
                        summary["deleted_count"] = cur.rowcount
                        conn.commit()
                    else:
                        summary["status"] = "skipped"
                        summary["reason"] = f"No user-identifying columns found on {table_name}"
        except Exception as e:
            logger.error("LiteLLM delete_spend_logs failed: %s", e)
            summary["status"] = "failed"
            summary["errors"].append(str(e))

        return summary
