"""Verify the packaged zlib and reproduce CVE-2026-76844 in a child process."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import mmap
import os
import signal
import subprocess
import sys
import zlib
from pathlib import Path

EXPECTED_PACKAGE = "1:1.3.dfsg+really1.3.2-4"
EXPECTED_RUNTIME = "1.3.2"
LIBRARY = Path("/usr/lib/x86_64-linux-gnu/libz.so.1.3.2")


def verify_inventory(root: Path = Path("/")) -> None:
    status_dir = root / "var/lib/dpkg/status.d"
    records = [p.read_text() for p in status_dir.iterdir() if not p.name.endswith(".md5sums")]
    zlib_records = [r for r in records if "Package: zlib1g\n" in r]
    if len(zlib_records) != 1 or f"Version: {EXPECTED_PACKAGE}\n" not in zlib_records[0]:
        raise RuntimeError("Expected exactly one authentic corrected zlib1g package record")
    library = root / LIBRARY.relative_to("/")
    checksums = (status_dir / "zlib1g.md5sums").read_text().splitlines()
    expected = next((line.split()[0] for line in checksums if line.split()[-1] == str(LIBRARY.relative_to("/"))), None)
    if not expected or hashlib.md5(library.read_bytes(), usedforsecurity=False).hexdigest() != expected:
        raise RuntimeError("zlib binary does not match the official package inventory")
    libraries = set(library.parent.glob("libz.so.1.*"))
    if libraries != {library} or (library.parent / "libz.so.1").resolve() != library.resolve():
        raise RuntimeError("Unexpected old zlib binary or SONAME target remains in the image")


def probe_failed_write() -> None:
    lib = ctypes.CDLL("libz.so.1", use_errno=True)
    lib.gzdopen.argtypes, lib.gzdopen.restype = [ctypes.c_int, ctypes.c_char_p], ctypes.c_void_p
    lib.gzwrite.argtypes, lib.gzwrite.restype = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int
    lib.gzerror.argtypes, lib.gzerror.restype = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)], ctypes.c_char_p
    lib.gzclearerr.argtypes, lib.gzclearerr.restype = [ctypes.c_void_p], None
    lib.gzclose.argtypes, lib.gzclose.restype = [ctypes.c_void_p], ctypes.c_int
    fd = os.open("/dev/full", os.O_WRONLY)
    # Transparent mode guarantees input remains pending when /dev/full fails;
    # deflate may consume all compressed input before the failed write instead.
    stream = lib.gzdopen(fd, b"wbT")
    if not stream:
        os.close(fd)
        raise RuntimeError("gzdopen failed")
    try:
        size = 256 * 1024
        with mmap.mmap(-1, size + mmap.PAGESIZE) as data:
            address = ctypes.addressof(ctypes.c_char.from_buffer(data))
            libc = ctypes.CDLL(None, use_errno=True)
            libc.mprotect.argtypes, libc.mprotect.restype = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int], ctypes.c_int
            if libc.mprotect(address + size, mmap.PAGESIZE, 0) != 0:
                raise OSError(ctypes.get_errno(), "Cannot protect input guard page")
            if lib.gzwrite(stream, address, size) != 0:
                raise RuntimeError("The /dev/full write did not fail as required")
            error = ctypes.c_int()
            lib.gzerror(stream, ctypes.byref(error))
            if error.value != -1:  # Z_ERRNO, not a precondition/allocation failure.
                raise RuntimeError(f"Expected Z_ERRNO, got {error.value}")
            lib.gzclearerr(stream)
            print("retrying-after-failed-write", flush=True)
            if lib.gzwrite(stream, b"12345678", 8) != 8:
                raise RuntimeError("The corrected buffered retry did not accept eight bytes")
            print("buffered-retry-passed", flush=True)
    finally:
        lib.gzclose(stream)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--expect-vulnerable", action="store_true")
    args = parser.parse_args()
    if args.probe:
        probe_failed_write()
        return
    if not args.expect_vulnerable:
        verify_inventory()
        if zlib.ZLIB_RUNTIME_VERSION != EXPECTED_RUNTIME:
            raise RuntimeError(f"Unexpected Python zlib runtime: {zlib.ZLIB_RUNTIME_VERSION}")
    probe = subprocess.run([sys.executable, __file__, "--probe"], capture_output=True, text=True, timeout=20)
    if args.expect_vulnerable:
        if probe.returncode != -signal.SIGSEGV or "retrying-after-failed-write" not in probe.stdout:
            raise RuntimeError(f"zlib {zlib.ZLIB_RUNTIME_VERSION} did not reproduce the expected memory fault: {probe}")
    elif probe.returncode or "buffered-retry-passed" not in probe.stdout:
        raise RuntimeError(f"zlib failed the memory-safety regression: {probe}")
    print(json.dumps({"runtime": zlib.ZLIB_RUNTIME_VERSION, "package": None if args.expect_vulnerable else EXPECTED_PACKAGE,
                      "regression": "old-image-rejected" if args.expect_vulnerable else "passed"}))


if __name__ == "__main__":
    main()
