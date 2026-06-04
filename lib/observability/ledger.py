import hashlib
import json
import os
from typing import Any, Dict, List, Optional


class TamperEvidentLedger:
    """A hash-chained append-only record ledger for tamper-evident auditing."""

    def __init__(self, filepath: Optional[str] = None):
        if filepath is None:
            # Try to resolve hermes home
            hermes_home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
            filepath = os.path.join(hermes_home, "ledger.jsonl")
        self.filepath = filepath

    def append(self, record_type: str, payload: Dict[str, Any]) -> str:
        """Append a record to the ledger. Returns the hash of the appended record."""
        # 1. Read last record to get its hash
        prev_hash = self._get_last_hash()

        # 2. Prepare the new record
        record = {
            "type": record_type,
            "payload": payload,
            "prev_hash": prev_hash,
        }

        # 3. Compute hash of the new record
        record_hash = self._hash_record(record)
        record["hash"] = record_hash

        # 4. Append to file
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return record_hash

    def verify(self) -> bool:
        """Verify the integrity of the entire ledger.

        Returns True if the ledger is consistent, False otherwise.
        """
        if not os.path.exists(self.filepath):
            return True  # Empty ledger is valid

        records = self._read_all_records()
        if not records:
            return True

        expected_prev_hash = "0" * 64
        for rec in records:
            # Check fields
            if "hash" not in rec or "prev_hash" not in rec:
                return False

            # Check previous hash connection
            if rec["prev_hash"] != expected_prev_hash:
                return False

            # Verify current hash matches recalculated hash
            actual_rec = {
                "type": rec["type"],
                "payload": rec["payload"],
                "prev_hash": rec["prev_hash"],
            }
            computed_hash = self._hash_record(actual_rec)
            if rec["hash"] != computed_hash:
                return False

            expected_prev_hash = rec["hash"]

        return True

    def _hash_record(self, record: Dict[str, Any]) -> str:
        # Standardized representation for hashing: sort keys
        serialized = json.dumps(record, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _get_last_hash(self) -> str:
        if not os.path.exists(self.filepath):
            return "0" * 64
        records = self._read_all_records()
        if not records:
            return "0" * 64
        return records[-1].get("hash") or "0" * 64

    def _read_all_records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.filepath):
            return []
        records = []
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
        return records
