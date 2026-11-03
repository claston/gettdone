"""Run the complete backend suite with a bounded memory budget and retained timings."""
import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def available_memory_mb():
    """Available RAM, also bounded by commit/cgroup headroom when known."""
    try:
        if sys.platform == "win32":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in (
                        "total_physical", "available_physical", "total_pagefile", "available_pagefile",
                        "total_virtual", "available_virtual", "available_extended_virtual",
                    )
                ]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return min(status.available_physical, status.available_pagefile) // (1024 * 1024)
        values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        available = int(values["MemAvailable"].split()[0]) // 1024
        maximum = Path("/sys/fs/cgroup/memory.max")
        current = Path("/sys/fs/cgroup/memory.current")
        if maximum.exists() and current.exists():
            limit = maximum.read_text().strip()
            if limit != "max":
                available = min(available, max(0, int(limit) - int(current.read_text())) // (1024 * 1024))
        return available
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def choose_workers(requested, *, cpu_count, available_mb):
    if requested != "auto":
        if not str(requested).isdigit() or int(requested) < 1:
            raise ValueError("PYTEST_WORKERS/--workers must be 'auto' or a positive integer")
        return int(requested)
    if available_mb is None:
        return 1
    # Reserve 512 MiB for the controller/OS and 640 MiB per worker. Serial mode
    # avoids an extra pytest controller when the machine cannot afford workers.
    return max(1, min(4, cpu_count or 1, (available_mb - 512) // 640))


@contextmanager
def suite_lock(path):
    """An OS lock is released even if the runner crashes; no stale PID cleanup."""
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("A backend test suite is already running for this checkout; wait for it to finish") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def cleanup_temp(path, temp_root):
    resolved = path.resolve()
    if path.is_symlink() or resolved.parent != temp_root.resolve() or not resolved.name.startswith("gettdone-pytest-run-"):
        raise ValueError("Refusing to delete a directory not owned by this test run")
    if resolved.exists():
        shutil.rmtree(resolved)


def run(argv=None, *, execute=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", default=os.getenv("PYTEST_WORKERS", "auto"), help="auto (default) or an explicit process count")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "tmp" / "test-runs")
    options, pytest_args = parser.parse_known_args(argv)
    owned = ("--basetemp", "--junitxml", "--junit-xml", "-n", "--numprocesses")
    if any(arg.split("=", 1)[0] in owned or arg.startswith("-n") for arg in pytest_args):
        parser.error("Use --workers and --report-dir; the runner owns worker count, temporary storage and JUnit output")
    memory = available_memory_mb()
    workers = choose_workers(options.workers, cpu_count=os.cpu_count(), available_mb=memory)
    temp_root = Path(tempfile.gettempdir()).resolve()
    lock_id = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    with suite_lock(temp_root / f"gettdone-pytest-{lock_id}.lock"):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        report = options.report_dir.resolve() / run_id
        report.mkdir(parents=True)
        base = Path(tempfile.mkdtemp(prefix="gettdone-pytest-run-", dir=temp_root))
        command = [sys.executable, "-m", "pytest", "-q", "-n", str(workers if workers > 1 else 0),
                   "--dist=worksteal", "--durations=20", "--junitxml", str(report / "junit.xml"), "--basetemp", str(base),
                   *pytest_args]
        print(f"Backend tests: {workers} process(es); available memory: {memory} MiB; "
              f"selection: {pytest_args or 'complete suite'}", flush=True)
        print(f"Results: {report}", flush=True)
        if options.workers != "auto" and workers > choose_workers("auto", cpu_count=os.cpu_count(), available_mb=memory):
            print("Explicit worker override exceeds the automatic memory budget.", flush=True)
        started = time.perf_counter()
        exit_code = 1
        try:
            exit_code = execute(command, cwd=ROOT).returncode
            return exit_code
        except KeyboardInterrupt:
            exit_code = 130
            return exit_code
        finally:
            result = {"workers": workers, "available_memory_mb": memory, "command": command,
                      "exit_code": exit_code, "elapsed_seconds": round(time.perf_counter() - started, 3)}
            try:
                (report / "run.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            finally:
                cleanup_temp(base, temp_root)


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
