"""Verify corrected official glibc files and the library actually in use."""
from __future__ import annotations

import ctypes
import hashlib
import json
import socket
from pathlib import Path

EXPECTED_PACKAGE = "2.43-7"
EXPECTED_RUNTIME = "2.43"
REQUIRED_FILES = {
    "libc6": [f"usr/lib/x86_64-linux-gnu/{name}" for name in (
        "libc.so.6", "ld-linux-x86-64.so.2", "libresolv.so.2", "libm.so.6",
    )],
    "libc-gconv-modules-extra": ["usr/lib/x86_64-linux-gnu/gconv/IBM850.so"],
}


def verify_inventory(root: Path = Path("/")) -> None:
    status_dir = root / "var/lib/dpkg/status.d"
    records = [path.read_text() for path in status_dir.iterdir() if not path.name.endswith(".md5sums")]
    for package, required in REQUIRED_FILES.items():
        matching = [record for record in records if f"Package: {package}\n" in record]
        if len(matching) != 1 or f"Version: {EXPECTED_PACKAGE}\n" not in matching[0]:
            raise RuntimeError(f"Expected one corrected official {package} package record")
        checksums = dict(line.split(maxsplit=1)[::-1] for line in
                         (status_dir / f"{package}.md5sums").read_text().splitlines() if line.strip())
        checksums = {name.strip(): digest for name, digest in checksums.items()}
        if not set(required) <= checksums.keys():
            raise RuntimeError(f"Missing required {package} library inventory")
        for relative, digest in checksums.items():
            target = root / relative
            if not target.is_file() or hashlib.md5(target.read_bytes(), usedforsecurity=False).hexdigest() != digest:
                raise RuntimeError(f"File does not match official {package} inventory: {relative}")


def main() -> None:
    verify_inventory()
    libc = ctypes.CDLL(None)
    libc.gnu_get_libc_version.restype = ctypes.c_char_p
    version = libc.gnu_get_libc_version().decode("ascii")
    if version != EXPECTED_RUNTIME:
        raise RuntimeError(f"Unexpected loaded glibc: {version}")
    # Exercise the resolver/NSS path without contacting an external service.
    if not socket.getaddrinfo("localhost", 443, type=socket.SOCK_STREAM):
        raise RuntimeError("Local hostname resolution failed")
    print(json.dumps({"package": EXPECTED_PACKAGE, "runtime": version,
                      "inventory": "passed", "localhost_resolution": "passed"}))


if __name__ == "__main__":
    main()
