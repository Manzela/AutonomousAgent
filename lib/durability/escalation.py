"""24h Telegram silence watcher. Runs periodically (sidecar) — scans Hermes Kanban for
blocked cards with stale last_heartbeat_at and emits escalation alerts.

Consumes config/limits.yaml agent.telegram_escalation_timeout_h.
"""

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Default Kanban DB path. The container runs as user `hermes` (uid 1000)
# with HOME=/home/hermes after the α-5 security-hardening PR. Hermes
# resolves its DB via ``Path.home() / ".hermes" / "kanban.db"`` —
# verified live at ``/home/hermes/.hermes/kanban.db`` (no ``kanban/``
# subdir; see also ``lib/kanban/telegram_bridge._DEFAULT_DB_PATH``).
KANBAN_DB_PATH = os.environ.get("HERMES_KANBAN_DB", "/home/hermes/.hermes/kanban.db")


def find_stale_blocked_cards(
    threshold_h: int = 24, db_path: str = None
) -> List[Tuple[int, str, float]]:
    """Return [(card_id, title, last_heartbeat_age_h), ...] for cards stuck in blocked
    longer than threshold_h hours."""
    db_path = db_path or KANBAN_DB_PATH
    if not Path(db_path).exists():
        return []
    now = time.time()
    threshold_s = threshold_h * 3600
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, last_heartbeat_at FROM tasks "
            "WHERE status = 'blocked' AND (? - last_heartbeat_at) > ?",
            (now, threshold_s),
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1], (now - r[2]) / 3600) for r in rows]


def emit_escalation(card_id: int, title: str, age_h: float) -> None:
    """Send a Telegram alert for a card stuck in ``blocked`` past the SLA.

    Goes through ``telegram_bridge.send_alert`` so escalations share the
    same publish path (and the same fail-open guarantees) as Kanban
    status-transition alerts. Local import avoids forcing the kanban
    package on the import path when this module is loaded by callers
    that don't care about Telegram (e.g. ``find_stale_blocked_cards``
    used standalone).
    """
    msg = (
        f"⚠️ Card {card_id} '{title}' blocked >24h ({age_h:.1f}h). "
        f"Use `/resume {card_id}` or `/cancel {card_id}`."
    )
    try:
        from lib.kanban.telegram_bridge import send_alert

        send_alert(card_id, msg)
    except Exception as exc:  # noqa: BLE001 — watcher must keep ticking
        logger.warning(
            "escalation: send_alert failed card=%s err=%s — original msg=%s",
            card_id,
            exc,
            msg,
        )


def run_once(threshold_h: int = 24, db_path: str = None) -> int:
    stale = find_stale_blocked_cards(threshold_h=threshold_h, db_path=db_path)
    for card_id, title, age_h in stale:
        emit_escalation(card_id, title, age_h)
    return len(stale)


if __name__ == "__main__":
    n = run_once()
    print(f"escalated {n} card(s)")
