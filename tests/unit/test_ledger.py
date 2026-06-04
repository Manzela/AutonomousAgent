import json
from lib.observability.ledger import TamperEvidentLedger


def test_ledger_append_and_verify(tmp_path):
    filepath = str(tmp_path / "test_ledger.jsonl")
    ledger = TamperEvidentLedger(filepath=filepath)

    # Empty ledger should verify as True
    assert ledger.verify() is True

    # Append first record
    hash1 = ledger.append("test_type", {"data": "first"})
    assert len(hash1) == 64

    # Verify after one record
    assert ledger.verify() is True

    # Append second record
    hash2 = ledger.append("test_type", {"data": "second"})
    assert len(hash2) == 64
    assert hash1 != hash2

    # Verify after two records
    assert ledger.verify() is True

    # Read records from file and check structure
    with open(filepath, "r") as f:
        lines = f.readlines()
    assert len(lines) == 2

    rec1 = json.loads(lines[0])
    rec2 = json.loads(lines[1])

    assert rec1["payload"] == {"data": "first"}
    assert rec1["prev_hash"] == "0" * 64
    assert rec1["hash"] == hash1

    assert rec2["payload"] == {"data": "second"}
    assert rec2["prev_hash"] == hash1
    assert rec2["hash"] == hash2


def test_ledger_tampering_detection(tmp_path):
    filepath = str(tmp_path / "test_ledger.jsonl")
    ledger = TamperEvidentLedger(filepath=filepath)

    ledger.append("test_type", {"data": "1"})
    ledger.append("test_type", {"data": "2"})
    ledger.append("test_type", {"data": "3"})

    assert ledger.verify() is True

    # Tamper with the file: change data in second record
    with open(filepath, "r") as f:
        lines = f.readlines()

    rec = json.loads(lines[1])
    rec["payload"]["data"] = "tampered"
    lines[1] = json.dumps(rec) + "\n"

    with open(filepath, "w") as f:
        f.writelines(lines)

    # Verification should now fail
    assert ledger.verify() is False
