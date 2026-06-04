from __future__ import annotations
import asyncio
import logging
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Optional

from app.core.sandbox import AbstractSandbox, SandboxResult

logger = logging.getLogger(__name__)


class LocalSubprocessSandbox(AbstractSandbox):
    """POSIX-rlimit subprocess sandbox. Dev/CI grade.

    Hard refusal: `network_allowed=True` raises immediately. This sandbox
    cannot enforce network isolation (it has no netns); accepting that flag
    would silently lie to callers about isolation properties.
    """

    is_production_grade: bool = False
    network_disabled: bool = True

    async def run(
        self,
        *,
        cmd: list[str],
        workdir: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        stdin: Optional[str] = None,
        timeout_s: float = 60.0,
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_files: int = 256,
        network_allowed: bool = False,
    ) -> SandboxResult:
        if network_allowed:
            raise PermissionError(
                "LocalSubprocessSandbox cannot enforce network isolation. "
                "Use FirecrackerSandbox for network_allowed=True."
            )
        if sys.platform not in ("linux", "darwin"):
            raise RuntimeError(f"LocalSubprocessSandbox is POSIX-only; platform={sys.platform}")

        with tempfile.TemporaryDirectory(prefix="sandbox-") as td:
            cwd = workdir or Path(td)
            cwd.mkdir(parents=True, exist_ok=True)

            # Build and inject the customized import hook and write protection policy
            site_dir = Path(td) / "site"
            site_dir.mkdir(parents=True, exist_ok=True)

            sitecustomize_code = """import builtins
import io
import os
import sys
import types
from pathlib import Path

allowed_dir_str = os.environ.get("SANDBOX_ALLOWED_DIR")
if allowed_dir_str:
    ALLOWED_DIR = Path(allowed_dir_str).resolve()
else:
    ALLOWED_DIR = Path.cwd().resolve()

TEMP_DIRS = [
    Path(os.environ.get("TMPDIR", "/tmp")).resolve(),
    Path("/tmp").resolve(),
    Path("/var/folders").resolve(),
    Path("/private/var").resolve(),
]

def is_path_allowed_for_write(path):
    try:
        p = Path(path).resolve()
        if p == ALLOWED_DIR or ALLOWED_DIR in p.parents:
            return True
        for td in TEMP_DIRS:
            if p == td or td in p.parents:
                return True
        return False
    except Exception:
        return False

# Hook open functions
original_open = builtins.open
original_io_open = io.open

def secure_open(file, mode='r', *args, **kwargs):
    if any(c in mode for c in ('w', 'a', 'x', '+')):
        if not is_path_allowed_for_write(file):
            raise PermissionError(f"Sandbox Violation: Write access denied for {file}")
    return original_open(file, mode, *args, **kwargs)

def secure_io_open(file, mode='r', *args, **kwargs):
    if any(c in mode for c in ('w', 'a', 'x', '+')):
        if not is_path_allowed_for_write(file):
            raise PermissionError(f"Sandbox Violation: Write access denied for {file}")
    return original_io_open(file, mode, *args, **kwargs)

builtins.open = secure_open
io.open = secure_io_open

original_os_open = os.open
def secure_os_open(path, flags, mode=0o777):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | getattr(os, "O_TEMPORARY", 0)
    if flags & write_flags:
        if not is_path_allowed_for_write(path):
            raise PermissionError(f"Sandbox Violation: Write access denied for {path}")
    return original_os_open(path, flags, mode)
os.open = secure_os_open

for name in ('remove', 'unlink', 'rmdir', 'mkdir', 'makedirs', 'rename', 'replace', 'symlink', 'link', 'chmod'):
    original_fn = getattr(os, name, None)
    if original_fn:
        def make_secure_fn(orig_fn, fn_name):
            def secure_fn(path, *args, **kwargs):
                paths_to_check = [path]
                if fn_name in ('rename', 'replace', 'symlink', 'link') and args:
                    paths_to_check.append(args[0])
                for p in paths_to_check:
                    if not is_path_allowed_for_write(p):
                        raise PermissionError(f"Sandbox Violation: Modifying {p} denied")
                return orig_fn(path, *args, **kwargs)
            return secure_fn
        setattr(os, name, make_secure_fn(original_fn, name))

# Define SecureModule type and sys.meta_path import hook
class SecureModule(types.ModuleType):
    def __setattr__(self, name, value):
        mod_name = getattr(self, "__name__", "")
        if mod_name.startswith(("tests", "app", "lib")):
            mod_file = getattr(self, "__file__", None)
            if mod_file and not is_path_allowed_for_write(mod_file):
                raise PermissionError(f"Sandbox Violation: Modifying attributes of parent module {mod_name} is blocked")
        super().__setattr__(name, value)

class SecureLoader:
    def __init__(self, original_loader):
        self.original_loader = original_loader

    def __getattr__(self, name):
        return getattr(self.original_loader, name)

    def create_module(self, spec):
        if hasattr(self.original_loader, "create_module"):
            mod = self.original_loader.create_module(spec)
        else:
            mod = None
        if mod is None:
            mod = SecureModule(spec.name)
        else:
            try:
                mod.__class__ = SecureModule
            except Exception:
                pass
        return mod

    def exec_module(self, module):
        self.original_loader.exec_module(module)

class SecureMetaFinder:
    def __init__(self, original_finder):
        self.original_finder = original_finder

    def find_spec(self, fullname, path, target=None):
        try:
            spec = self.original_finder.find_spec(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = SecureLoader(spec.loader)
            return spec
        except Exception:
            return None

sys.meta_path = [SecureMetaFinder(f) if hasattr(f, "find_spec") else f for f in sys.meta_path]

# Update already loaded modules
for name, mod in list(sys.modules.items()):
    if name.startswith(("tests", "app", "lib")) and isinstance(mod, types.ModuleType):
        try:
            mod.__class__ = SecureModule
        except Exception:
            pass

# Block Python dynamic mocks of parent test frameworks
try:
    import unittest.mock
    original_patch = unittest.mock.patch
    def secure_patch(*args, **kwargs):
        target = args[0] if args else None
        if isinstance(target, str):
            if target.startswith(("tests", "app", "lib")):
                raise PermissionError(f"Sandbox Violation: Patching target {target} is blocked inside the sandbox")
        elif target is not None:
            mod = getattr(target, "__module__", "")
            if mod.startswith(("tests", "app", "lib")):
                raise PermissionError(f"Sandbox Violation: Patching target in module {mod} is blocked inside the sandbox")
        return original_patch(*args, **kwargs)
    unittest.mock.patch = secure_patch

    for attr in dir(original_patch):
        if not attr.startswith('__'):
            try:
                setattr(secure_patch, attr, getattr(original_patch, attr))
            except AttributeError:
                pass

    original_patch_object = unittest.mock.patch.object
    def secure_patch_object(target, attribute, *args, **kwargs):
        mod = getattr(target, "__module__", "")
        if isinstance(target, types.ModuleType):
            mod = getattr(target, "__name__", "")
        if mod.startswith(("tests", "app", "lib")):
            raise PermissionError(f"Sandbox Violation: Patching object in module {mod} is blocked inside the sandbox")
        return original_patch_object(target, attribute, *args, **kwargs)
    unittest.mock.patch.object = secure_patch_object
except ImportError:
    pass
"""
            (site_dir / "sitecustomize.py").write_text(sitecustomize_code, encoding="utf-8")

            child_env = dict(env) if env is not None else dict(os.environ)
            project_root = str(Path.cwd().resolve())
            existing_pythonpath = child_env.get("PYTHONPATH", "")
            if existing_pythonpath:
                child_env["PYTHONPATH"] = (
                    f"{site_dir}{os.pathsep}{project_root}{os.pathsep}{existing_pythonpath}"
                )
            else:
                child_env["PYTHONPATH"] = f"{site_dir}{os.pathsep}{project_root}"
            child_env["SANDBOX_ALLOWED_DIR"] = str(cwd.resolve())

            if sys.platform != "win32":
                wrapper_code = (
                    "import sys, os, resource; "
                    f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_seconds}, {cpu_seconds + 5})); "
                    f"as_bytes = {memory_mb} * 1024 * 1024; "
                    f"resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes)) if sys.platform != 'darwin' else None; "
                    f"resource.setrlimit(resource.RLIMIT_NOFILE, ({max_files}, {max_files})); "
                    "os.execvp(sys.argv[1], sys.argv[1:])"
                )
                wrapped_cmd = [sys.executable, "-c", wrapper_code] + list(cmd)
                proc = await asyncio.create_subprocess_exec(
                    *wrapped_cmd,
                    cwd=str(cwd),
                    env=child_env,
                    stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,  # for killpg cleanup
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(cwd),
                    env=child_env,
                    stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,  # for killpg cleanup
                )
            t0 = _now()
            killed = False
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(stdin.encode("utf-8") if stdin else None),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                killed = True
                # Kill the process group so we get any forked children.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    stdout_b, stderr_b = await proc.communicate()
                except Exception as _exc:  # noqa: BLE001
                    logger.debug(
                        "sandbox: proc.communicate() failed after SIGKILL; "
                        "discarding output (pid=%s, exc=%r)",
                        proc.pid,
                        _exc,
                    )
                    stdout_b, stderr_b = b"", b""
            duration = _now() - t0
            rc = proc.returncode if proc.returncode is not None else -1
            return SandboxResult(
                returncode=rc,
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
                duration_s=duration,
                killed=killed,
            )


# preexec_fn is unsafe in multi-threaded processes (P3-11), so limits are wrapped inline via os.execvp above.


def _now() -> float:
    import time as _t

    return _t.monotonic()
