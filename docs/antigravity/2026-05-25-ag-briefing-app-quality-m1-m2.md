# Antigravity Briefing — AG-1: app/ Code Quality Fixes (M1, M2, L2)
**Date:** 2026-05-25
**Model:** Claude Opus 4.6 Thinking (recommended) or Gemini 3.1 Pro Preview
**Priority:** MEDIUM — security correctness (M1) + developer ergonomics (M2, L2)
**Collision boundary:** You own `app/adapters/` and `app/core/{memory,sandbox,embedder}.py` exclusively.
**Do NOT touch:**
- `app/core/orchestrator.py` (Claude Code agent working on it right now)
- `app/tests/test_peer_dispatch.py` (same agent)
- `lib/a2a/` (Claude Code territory — never touch)

---

## 1. Context

A comprehensive security audit identified code quality gaps in the `app/` seed orchestrator files. All three fixes are in files NOT touched by any currently-running agent.

**Research to read first:**
1. `app/adapters/inmemory/sandbox.py` — read in full; understand the `_make_preexec` function and why it runs in a forked child process
2. `app/core/memory.py`, `app/core/sandbox.py`, `app/core/embedder.py` — read all abstract method bodies

---

## 2. Fix M1 — rlimit failures in sandbox.py lines 104, 109 (HIGH severity)

### The Problem

In `app/adapters/inmemory/sandbox.py`, inside `_make_preexec()`, there are two `except ValueError: pass` blocks:

```python
try:
    resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
except ValueError:
    pass  # ← DANGEROUS SILENT FAILURE

try:
    resource.setrlimit(resource.RLIMIT_NOFILE, (max_files, max_files))
except ValueError:
    pass  # ← DANGEROUS SILENT FAILURE
```

**Why this is dangerous:** `_make_preexec()` returns a function that runs in the forked child process (via `preexec_fn=preexec` in `create_subprocess_exec`). If `setrlimit` fails, the child process starts with **no memory or file-descriptor constraints** — the core isolation guarantee is silently broken. The caller has no indication.

**Why `logging` is UNSAFE here:** The `preexec_fn` runs post-fork, before exec. In this state:
- All file descriptors from the parent are inherited, including log file handles
- Python's logging machinery may have locks held by other threads at fork time → deadlock
- The safe way to write from preexec is `os.write(2, msg.encode())` (raw write to stderr fd=2) or `sys.stderr.write(msg)` (usually safe for simple cases)

### The Fix

Replace both `except ValueError: pass` blocks:

```python
try:
    resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
except ValueError as exc:
    # Log to stderr raw — logging is unsafe in preexec_fn (post-fork, pre-exec)
    sys.stderr.write(
        f"sandbox: WARNING: RLIMIT_AS ({as_bytes // (1024*1024)}MB) rejected by OS: {exc!r}; "
        f"process will start WITHOUT memory isolation\n"
    )
try:
    resource.setrlimit(resource.RLIMIT_NOFILE, (max_files, max_files))
except ValueError as exc:
    sys.stderr.write(
        f"sandbox: WARNING: RLIMIT_NOFILE ({max_files} files) rejected by OS: {exc!r}; "
        f"process will start WITHOUT file-descriptor isolation\n"
    )
```

`sys` is already imported at the top of the file.

---

## 3. Fix L2 — os.setsid() OSError comment in sandbox.py line 115

The `except OSError: pass` on `os.setsid()` is actually correct behavior, but it's confusing without a comment.

```python
try:
    os.setsid()
except OSError:
    pass  # already a session leader — expected when start_new_session=True was set
          # on create_subprocess_exec (which already called setsid before preexec)
```

---

## 4. Fix M2 — ABC methods: `...` → `raise NotImplementedError` (MEDIUM severity)

### The Problem

In `app/core/memory.py`, `app/core/sandbox.py`, and `app/core/embedder.py`, all abstract method bodies use `...` (ellipsis):

```python
@abstractmethod
async def put(self, record: MemoryRecord) -> None: ...
```

**Why this matters (per CLAUDE.md builder-agent rule):**
- The ABCs are the canonical contract that builder agents must implement exactly
- If a subclass forgets to override a method, `...` returns `None` silently — the ABC machinery catches this at instantiation, but during development, partial concrete classes can slip through
- `raise NotImplementedError` provides a clear error message and is the Google Python Style Guide recommendation
- CodeQL `py/ineffectual-statement` fires on every `...` as a note — this eliminates them cleanly

### Files to modify

**`app/core/memory.py`** — find all `@abstractmethod` methods. Replace each `...` body with:
```python
raise NotImplementedError(f"{self.__class__.__name__}.put() must be implemented")
```
Adapt the method name accordingly for each method (`put`, `search`, `get`, `delete`, `gc_expired`).

**`app/core/sandbox.py`** — find the `run()` abstractmethod:
```python
raise NotImplementedError(f"{self.__class__.__name__}.run() must be implemented")
```

**`app/core/embedder.py`** — find `dim` property and `embed()`:
```python
@property
@abstractmethod
def dim(self) -> int:
    raise NotImplementedError(f"{self.__class__.__name__}.dim must be implemented")

@abstractmethod
def embed(self, text: str) -> np.ndarray:
    raise NotImplementedError(f"{self.__class__.__name__}.embed() must be implemented")
```

**Important:** Do NOT change `embed_many()` in `AbstractEmbedder` — it has a real implementation (calls `self.embed()`), not a `...` stub.

---

## 5. Execution

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git checkout main && git pull
git checkout -b fix/app-code-quality-m1-m2

# Read all target files first
# Then make all 3 fixes

# Run existing tests to verify no regressions
uv sync --extra a2a --extra dev
uv run pytest app/tests/ -v 2>&1 | tail -15

# Lint
uv run ruff check app/ && uv run ruff format app/

# Verify the in-memory adapters still pass
# (the InMemoryStore, LocalSubprocessSandbox, and InMemoryEmbedder all implement the ABCs)
uv run pytest app/tests/test_inmemory_adapters.py -v

# Commit
git add app/adapters/inmemory/sandbox.py app/core/memory.py app/core/sandbox.py app/core/embedder.py
git commit -m "fix(app): sandbox rlimit silent failure + ABC NotImplementedError + setsid comment

M1: app/adapters/inmemory/sandbox.py — rlimit setrlimit failures (RLIMIT_AS,
    RLIMIT_NOFILE) now write to sys.stderr instead of silently passing. These
    run inside preexec_fn (post-fork, pre-exec) where logging is unsafe; the
    warnings alert operators that sandbox isolation may be degraded.

M2: app/core/{memory,sandbox,embedder}.py — replace '...' (ellipsis) in all
    @abstractmethod bodies with raise NotImplementedError(class.method).
    Matches Google Python Style Guide; eliminates CodeQL py/ineffectual-statement;
    prevents silent None returns from incomplete subclasses.

L2: app/adapters/inmemory/sandbox.py — add comment to OSError pass on
    os.setsid() explaining why it is expected."

git push -u origin fix/app-code-quality-m1-m2

gh pr create \
  --title "fix(app): sandbox rlimit logging + ABC NotImplementedError pattern" \
  --base main \
  --body "## Summary
Security audit (M1) and code quality (M2, L2) fixes in app/ layer.

**M1 (HIGH):** sandbox.py preexec_fn now writes to sys.stderr when rlimit
calls fail, so operators know isolation may be degraded. Previously silent.

**M2 (MEDIUM):** ABC abstract methods now raise NotImplementedError instead
of returning ellipsis, preventing silent None returns from incomplete subclasses.

**L2 (LOW):** Added comment to os.setsid() OSError pass.

All app/tests/ pass. No lib/a2a/ files touched."
```

---

## 6. Acceptance Criteria

```bash
# All app/ tests pass (including inmemory adapter tests)
uv run pytest app/tests/ -v  # all pass

# No lib/a2a/ files touched
git diff --name-only main | grep "lib/a2a" && echo "ERROR" || echo "CLEAN"

# Verify NotImplementedError raises correctly
python3 -c "
from app.core.memory import AbstractMemoryStore
class Bad(AbstractMemoryStore): pass
try:
    Bad()
    print('ERROR: should have raised')
except TypeError:
    print('PASS: ABC enforcement works')
"

# CI green
gh pr checks --watch
```
