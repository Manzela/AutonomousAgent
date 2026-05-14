# Code Style

## Python

- Tooling: [`ruff`](https://docs.astral.sh/ruff/) for lint + format. Config in `pyproject.toml`.
- Line length: 100.
- Target: Python 3.11+.
- Type hints: required for all public functions, methods, and dataclass fields.
- Docstrings: short. One sentence for purpose, parameters/returns only if non-obvious.
- Imports: sorted by ruff (`I` rules); first-party last.
- Avoid: `from x import *`, mutable default args, broad `except:` (use `except Exception:` minimum).

## Module layout

- One responsibility per module
- Public API at top of file (dataclasses, then public functions, then private)
- Helpers prefixed with `_`
- Tests live alongside the module they test in `tests/unit/test_<module>.py`

## Naming

- `snake_case` for functions, variables, modules
- `PascalCase` for classes
- `SCREAMING_SNAKE_CASE` for module-level constants
- Booleans named `is_*`, `has_*`, `should_*`
- Avoid abbreviations except universally understood ones (`url`, `api`, `id`)

## Comments

- Default to no comments. Code should be self-documenting via good naming.
- Write a comment only when the WHY is non-obvious (a hidden constraint, workaround, surprising behavior).
- Never write comments that restate the code. Never write multi-paragraph docstrings.
- TODO comments must include either a date or an issue reference: `# TODO(2026-06): refactor this once Honcho v2 lands`.

## Errors

- Raise specific exceptions; don't return `None` to signal an error
- Catch only what you can handle; let the rest bubble
- Always log errors at the boundary that catches them (don't double-log)

## Shell scripts

- Bash strict mode at top: `set -euo pipefail`
- Quote everything: `"$var"` not `$var`
- Use `[[ ]]` not `[ ]`
- No `eval` unless absolutely required and explained in a comment
- All scripts include `#!/usr/bin/env bash` shebang
- All scripts are executable (`chmod +x`)

## YAML

- 2-space indent, never tabs
- Comments only for non-obvious fields
- Lists on new lines for >2 items
- Use `null` explicitly, not blank

## Dockerfiles

- Pin base image to a specific tag (not `:latest`) at release time
- One concern per layer (don't combine unrelated `RUN` commands)
- Clean up apt caches in the same layer as `apt-get install`
- Run as non-root in production sandboxes
- Always set `WORKDIR`

## Secrets in code

- NEVER hardcode a secret, even a "test" one
- Use `os.environ[...]` (not `os.environ.get` with a default value that looks like a real secret)
- For tests, use string literals that the scrubber will catch and redact
