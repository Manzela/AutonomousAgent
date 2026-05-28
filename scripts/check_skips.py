#!/usr/bin/env python3
"""CI gate script to ensure all integration test skips are documented.

Required by P0-18.
"""

import sys
import yaml
import pytest


class SkipCollector:
    def __init__(self):
        self.skips = []

    def pytest_collection_modifyitems(self, items):
        for item in items:
            for m in item.iter_markers():
                if m.name in ("skip", "skipif"):
                    self.skips.append(item.nodeid)
                    break


def main():
    collector = SkipCollector()
    pytest.main(["--collect-only", "-q", "tests/integration/"], plugins=[collector])

    try:
        with open("tests/integration/SKIPS.yaml", "r", encoding="utf-8") as f:
            allowed = {entry["test"] for entry in yaml.safe_load(f)["skips"]}
    except FileNotFoundError:
        print("Error: tests/integration/SKIPS.yaml not found.")
        sys.exit(1)

    undocumented = [s for s in collector.skips if s not in allowed]
    if undocumented:
        print("Error: Undocumented skipped integration tests found:\n")
        for u in undocumented:
            print(f"  - {u}")
        print("\nPlease document them in tests/integration/SKIPS.yaml.")
        sys.exit(1)
    else:
        print("All integration test skips are documented.")
        sys.exit(0)


if __name__ == "__main__":
    main()
