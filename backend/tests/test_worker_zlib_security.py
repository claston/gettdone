"""Check that image evidence cannot accept stale metadata or an unchanged binary."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_worker_zlib", ROOT / "scripts/check_worker_zlib.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def inventory(tmp_path):
    status = tmp_path / "var/lib/dpkg/status.d"
    status.mkdir(parents=True)
    library = tmp_path / check.LIBRARY.relative_to("/")
    library.parent.mkdir(parents=True)
    library.write_bytes(b"fixture-only")
    (status / "zlib1g").write_text(f"Package: zlib1g\nVersion: {check.EXPECTED_PACKAGE}\n")
    checksum = hashlib.md5(library.read_bytes(), usedforsecurity=False).hexdigest()
    (status / "zlib1g.md5sums").write_text(f"{checksum}  {check.LIBRARY.relative_to('/')}\n")
    return status, library


def test_rejects_old_package_even_when_binary_has_new_name(tmp_path):
    status, _ = inventory(tmp_path)
    (status / "zlib1g").write_text("Package: zlib1g\nVersion: 1:1.3.dfsg+really1.3.1-1+b1\n")
    with pytest.raises(RuntimeError, match="package record"):
        check.verify_inventory(tmp_path)


def test_rejects_duplicate_package_record(tmp_path):
    status, _ = inventory(tmp_path)
    (status / "stale-zlib").write_text((status / "zlib1g").read_text())
    with pytest.raises(RuntimeError, match="package record"):
        check.verify_inventory(tmp_path)


def test_rejects_binary_that_does_not_match_package(tmp_path):
    _, library = inventory(tmp_path)
    library.write_bytes(b"different binary")
    with pytest.raises(RuntimeError, match="official package inventory"):
        check.verify_inventory(tmp_path)


def test_rejects_an_old_library_left_alongside_the_replacement(tmp_path):
    _, library = inventory(tmp_path)
    (library.parent / "libz.so.1.3.1").write_bytes(b"old binary")
    with pytest.raises(RuntimeError, match="old zlib"):
        check.verify_inventory(tmp_path)


@pytest.mark.parametrize("name", ["Dockerfile.lambda", "Dockerfile.lambda.debian"])
def test_both_worker_images_install_and_validate_the_corrected_package(name):
    dockerfile = (ROOT / name).read_text()
    assert "zlib1g=1:1.3.dfsg+really1.3.2-4" in dockerfile
    assert "check_worker_zlib.py" in dockerfile
    assert "zlib1g.md5sums" in dockerfile
