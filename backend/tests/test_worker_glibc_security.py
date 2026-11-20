"""Ensure the worker contains corrected binaries, not only changed labels."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["Dockerfile.lambda", "Dockerfile.lambda.debian"])
def test_worker_installs_corrected_glibc_with_matching_loader_and_conversion_modules(name):
    dockerfile = (ROOT / name).read_text(encoding="utf-8")
    for package in ("libc6", "libc-bin", "libc-gconv-modules-extra"):
        assert f"{package}=2.43-7" in dockerfile
    assert '"/opt/check_worker_glibc.py"' in dockerfile
    assert "libc6 libc-gconv-modules-extra" in dockerfile


@pytest.mark.parametrize("workflow", ["worker-debian-candidate.yml", "deploy-worker-ecr.yml"])
def test_ci_and_deploy_record_the_verified_glibc_runtime(workflow):
    content = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
    assert "/opt/check_worker_glibc.py > artifacts/glibc-runtime.json" in content
    assert "--ignore-unfixed" not in content


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location("check_worker_glibc", ROOT / "scripts/check_worker_glibc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory(root, checker):
    status = root / "var/lib/dpkg/status.d"
    status.mkdir(parents=True)
    for package, paths in checker.REQUIRED_FILES.items():
        (status / package).write_text(f"Package: {package}\nVersion: 2.43-7\n", encoding="utf-8")
        entries = []
        for relative in paths:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"synthetic-library")
            digest = hashlib.md5(target.read_bytes(), usedforsecurity=False).hexdigest()
            entries.append(f"{digest}  {relative}")
        (status / f"{package}.md5sums").write_text("\n".join(entries), encoding="utf-8")
    return status


def test_inventory_accepts_matching_files(tmp_path, checker):
    inventory(tmp_path, checker)
    checker.verify_inventory(tmp_path)


@pytest.mark.parametrize("fault", ["old_version", "duplicate_record", "old_loader", "old_resolver", "missing_modules"])
def test_inventory_rejects_stale_or_incomplete_runtime(tmp_path, checker, fault):
    status = inventory(tmp_path, checker)
    if fault == "old_version":
        (status / "libc6").write_text("Package: libc6\nVersion: 2.41-12+deb13u4\n", encoding="utf-8")
    elif fault == "duplicate_record":
        (status / "stale-libc6").write_bytes((status / "libc6").read_bytes())
    elif fault == "missing_modules":
        (tmp_path / checker.REQUIRED_FILES["libc-gconv-modules-extra"][0]).unlink()
    else:
        library = "ld-linux-x86-64.so.2" if fault == "old_loader" else "libresolv.so.2"
        (tmp_path / "usr/lib/x86_64-linux-gnu" / library).write_bytes(b"old-library")
    with pytest.raises(RuntimeError):
        checker.verify_inventory(tmp_path)
