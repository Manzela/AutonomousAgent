"""Validate config/limits.yaml against config/limits-schema.json.

Run as a module: `python -m lib.limits_validator config/limits.yaml`
Exits 0 on success, non-zero with errors on failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def load_schema(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def validate(config_path: Path, schema_path: Path) -> list[str]:
    """Return a list of error messages (empty list = valid)."""
    config = load_yaml(config_path)
    schema = load_schema(schema_path)
    validator = Draft202012Validator(schema)
    errors = []
    for error in validator.iter_errors(config):
        path = "/".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"{path}: {error.message}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m lib.limits_validator <path-to-limits.yaml>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1])
    schema_path = config_path.parent / "limits-schema.json"
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 2
    if not schema_path.exists():
        print(f"Schema not found: {schema_path}", file=sys.stderr)
        return 2

    errors = validate(config_path, schema_path)
    if errors:
        print(f"Validation FAILED for {config_path}:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"Validation OK: {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
