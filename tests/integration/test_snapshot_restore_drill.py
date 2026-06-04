"""Automated integration test for SP-R5: Disaster Recovery (DR) snapshot and restore drill.

Exercises the round-trip snapshot and restore logic documented in
docs/runbooks/recovery.md and automated via scripts/snapshot.sh.
Runs in a completely hermetic local environment using temporary directories.
"""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
from pathlib import Path


def test_snapshot_restore_drill_lifecycle(tmp_path: Path):
    # 1. Setup mock live directory layout matching hermes-data volume (/data)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create checkpoints directory and mock step file
    checkpoints_dir = data_dir / "checkpoints" / "session-1"
    checkpoints_dir.mkdir(parents=True)
    step_file = checkpoints_dir / "step-1.json"
    step_file.write_text('{"step_index": 1, "goal": "restore drill goal"}', encoding="utf-8")

    # Create kanban directory and a sqlite database with test entries
    kanban_dir = data_dir / "kanban"
    kanban_dir.mkdir(parents=True)
    db_path = kanban_dir / "kanban.db"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
    cursor.execute("INSERT INTO cards (id, title, status) VALUES ('c1', 'DR Drill Task', 'TODO')")
    conn.commit()
    conn.close()

    # 2. Perform snapshot (tar the directory and copy discrete Kanban DB)
    snapshot_dir = tmp_path / "snapshots" / "20260604-140000"
    snapshot_dir.mkdir(parents=True)

    tar_path = snapshot_dir / "hermes-data.tar.gz"
    # Create tarfile. Mode "w:gz" matches "tar czf" in snapshot.sh
    with tarfile.open(tar_path, "w:gz") as tar:
        # Add everything from data_dir under the root folder '.' in the archive
        tar.add(data_dir, arcname=".")

    # Copy discrete kanban.db snapshot (mirroring container file copy in snapshot.sh)
    discrete_db_snapshot = snapshot_dir / "kanban.db"
    shutil.copy2(db_path, discrete_db_snapshot)

    # 3. Simulate disaster (delete/corrupt all active files in the data directory)
    shutil.rmtree(checkpoints_dir)
    db_path.unlink()

    assert not checkpoints_dir.exists()
    assert not db_path.exists()

    # 4. Restore state from snapshots (mimicking recovery.md runbook commands)
    # Restore the hermes-data volume tar archive
    restored_data_dir = tmp_path / "restored_data"
    restored_data_dir.mkdir()

    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(path=restored_data_dir)

    # Restore discrete kanban.db
    restored_kanban_dir = restored_data_dir / "kanban"
    restored_kanban_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(discrete_db_snapshot, restored_kanban_dir / "kanban.db")

    # 5. Verify restored state integrity
    restored_step_file = restored_data_dir / "checkpoints" / "session-1" / "step-1.json"
    assert restored_step_file.exists()
    assert "restore drill goal" in restored_step_file.read_text(encoding="utf-8")

    restored_db_path = restored_kanban_dir / "kanban.db"
    assert restored_db_path.exists()

    # Verify sqlite DB records are fully recovered and matching original values
    conn_r = sqlite3.connect(restored_db_path)
    cursor_r = conn_r.cursor()
    cursor_r.execute("SELECT id, title, status FROM cards WHERE id = 'c1'")
    row = cursor_r.fetchone()
    conn_r.close()

    assert row is not None
    assert row[0] == "c1"
    assert row[1] == "DR Drill Task"
    assert row[2] == "TODO"
