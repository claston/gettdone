"""Keep the temporary QUIC assessment scoped to the reviewed worker runtime."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checker():
    spec = importlib.util.spec_from_file_location("check_worker_openssl", ROOT / "scripts/check_worker_openssl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reviewed_dependencies_reject_added_or_updated_packages(checker):
    checker.verify_distributions(checker.REVIEWED_DISTRIBUTIONS)
    for change in ({"aioquic": "1.0"}, {"urllib3": "99.0"}):
        with pytest.raises(RuntimeError):
            checker.verify_distributions(checker.REVIEWED_DISTRIBUTIONS | change)


@pytest.mark.parametrize("content", [b"OSSL_QUIC_server_method", b"SSL_new_listener", b"SSL_accept_connection"])
def test_native_quic_callers_require_new_review(tmp_path, checker, content):
    (tmp_path / "consumer.so").write_bytes(b"\x7fELF" + content)
    with pytest.raises(RuntimeError):
        checker.verify_native_callers([tmp_path])


def test_tls_only_native_caller_is_allowed(tmp_path, checker):
    (tmp_path / "consumer.so").write_bytes(b"\x7fELFSSL_CTX_new\0TLS_client_method")
    assert checker.verify_native_callers([tmp_path]) == 1


@pytest.mark.parametrize("content", [
    "import aioquic", "getattr(lib, 'OSSL_QUIC_server_method')()",
    "sock.bind(('0.0.0.0', 443))", "socket.socket(type=socket.SOCK_DGRAM)",
])
def test_application_network_server_changes_require_new_review(tmp_path, checker, content):
    (tmp_path / "worker.py").write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError):
        checker.verify_application(tmp_path)


def test_application_without_server_passes(tmp_path, checker):
    (tmp_path / "worker.py").write_text("client.get_object(Bucket='synthetic', Key='file')", encoding="utf-8")
    assert checker.verify_application(tmp_path) == 1


def test_empty_application_is_not_evidence(tmp_path, checker):
    with pytest.raises(RuntimeError):
        checker.verify_application(tmp_path)


def test_package_inventory_rejects_old_or_duplicate_records(tmp_path, checker):
    status = tmp_path / "var/lib/dpkg/status.d"
    status.mkdir(parents=True)
    package = status / "libssl3t64"
    package.write_text("Package: libssl3t64\nVersion: 3.5.7-1~deb13u3\n", encoding="utf-8")
    checker.verify_package(tmp_path)
    (status / "duplicate").write_bytes(package.read_bytes())
    with pytest.raises(RuntimeError):
        checker.verify_package(tmp_path)
    (status / "duplicate").unlink()
    package.write_text("Package: libssl3t64\nVersion: 3.5.6\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        checker.verify_package(tmp_path)


@pytest.mark.parametrize("name", ["worker-debian-candidate.yml", "deploy-worker-ecr.yml"])
def test_ci_and_deploy_require_and_preserve_the_applicability_assessment(name):
    workflow = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    assert "/opt/check_worker_openssl.py > artifacts/openssl-runtime.json" in workflow
    assert workflow.count("--openssl-evidence artifacts/openssl-runtime.json") == (2 if name.startswith("deploy") else 1)
    assert "--ignore-unfixed" not in workflow


@pytest.mark.parametrize("name", ["Dockerfile.lambda", "Dockerfile.lambda.debian"])
def test_both_worker_contexts_include_the_assessment_checker(name):
    assert "COPY scripts/check_worker_openssl.py /opt/check_worker_openssl.py" in (ROOT / name).read_text(encoding="utf-8")
    assert "!scripts/check_worker_openssl.py" in (ROOT / f"{name}.dockerignore").read_text(encoding="utf-8")
