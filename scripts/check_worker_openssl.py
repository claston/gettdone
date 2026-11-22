"""Record guardrails for the reviewed, temporary QUIC-server non-applicability assessment.

This does not patch OpenSSL or prove arbitrary code unreachable. It detects drift
from the reviewed Lambda client architecture; see docs/worker-openssl-security.md.
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import sys
from importlib import metadata
from pathlib import Path

EXPECTED_PACKAGE = "3.5.7-1~deb13u3"
EXPECTED_RUNTIME = "3.5.7"
ASSESSMENT = "lambda-client-no-quic-server-v1"
REVIEWED_DISTRIBUTIONS = {
    "annotated-types": "0.8.0", "awslambdaric": "4.2.2", "boto3": "1.39.8", "botocore": "1.39.17",
    "et-xmlfile": "2.0.0", "jmespath": "1.1.0", "openpyxl": "3.1.5", "pillow": "13.0.0",
    "psycopg": "3.2.9", "psycopg-binary": "3.2.9", "psycopg-pool": "3.3.3", "pydantic": "2.11.3",
    "pydantic-core": "2.33.1", "pypdf": "7.1.0", "pypdfium2": "5.8.0", "python-dateutil": "2.9.0.post0",
    "s3transfer": "0.13.1", "simplejson": "4.3.0", "six": "1.17.0", "snapshot-restore-py": "1.0.0",
    "typing-inspection": "0.4.5", "typing-extensions": "4.16.0", "urllib3": "2.8.0",
}
QUIC_SERVER_SYMBOLS = (b"OSSL_QUIC_server_method", b"SSL_new_listener", b"SSL_accept_connection")
SERVER_PATTERN = re.compile(
    r"quic|SSL_new_listener|SSL_accept_connection|SOCK_DGRAM|PROTOCOL_TLS_SERVER|\.\s*(?:bind|listen)\s*\(", re.I,
)
OPTIONAL_CRYPTO_PATTERN = re.compile(
    rb"OSSL_QUIC_|SSL_(?:CTX_)?dane_|SSL_(?:new_listener|accept_connection)|"
    rb"(?:AES[-_]\d+[-_]GCM[-_]SIV|EVP_aes_\d+_gcm_siv)|"
    rb"CMS_(?:encrypt|verify)|PKCS7_verify|PEM_X509_INFO_read", re.I,
)


def verify_optional_crypto(python_roots, native_roots):
    """Conservative drift check of application/dependencies and native consumers.

    Does not prove absence of dynamic calls or patch the library. Any reference,
    including documentation, requires a new review instead of silently accepting it.
    """
    counts = {"python_files": 0, "native_files": 0}
    for kind, roots, pattern in (("python_files", python_roots, "*.py"), ("native_files", native_roots, "*.so*")):
        for root in roots:
            for path in root.rglob(pattern):
                if not path.is_file():
                    continue
                if kind == "native_files" and path.name.startswith(("libssl", "libcrypto")):
                    continue  # Definitions are present; assess the consumers separately.
                data = path.read_bytes()
                if kind == "native_files" and not data.startswith(b"\x7fELF"):
                    continue
                counts[kind] += 1
                if OPTIONAL_CRYPTO_PATTERN.search(data):
                    raise RuntimeError(f"Optional OpenSSL API reference requires review: {path}")
    if not all(counts.values()):
        raise RuntimeError("Missing optional OpenSSL consumer inventory")
    return counts


def verify_distributions(installed):
    if installed != REVIEWED_DISTRIBUTIONS:
        changed = sorted(name for name in installed.keys() | REVIEWED_DISTRIBUTIONS.keys()
                         if installed.get(name) != REVIEWED_DISTRIBUTIONS.get(name))
        raise RuntimeError(f"Worker dependencies changed; renew the OpenSSL applicability assessment: {changed}")


def verify_application(root):
    files = list(root.rglob("*.py"))
    if not files:
        raise RuntimeError("Missing worker application source")
    for path in files:
        if SERVER_PATTERN.search(path.read_text(encoding="utf-8")):
            raise RuntimeError(f"Network server/QUIC source requires review: {path}")
    return len(files)


def verify_native_callers(roots):
    count = 0
    for root in roots:
        for path in root.rglob("*.so*"):
            if not path.is_file():
                continue
            # OpenSSL implements the vulnerable API. Inspect its consumers, not
            # its definitions; never claim the affected library has been patched.
            if path.name.startswith(("libssl", "libcrypto")):
                continue
            data = path.read_bytes()
            if data.startswith(b"\x7fELF"):
                count += 1
                if any(symbol in data for symbol in QUIC_SERVER_SYMBOLS):
                    raise RuntimeError(f"Native QUIC server API reference requires review: {path}")
    if not count:
        raise RuntimeError("Missing native consumer inventory")
    return count


def verify_package(root=Path("/")):
    records = [path.read_text() for path in (root / "var/lib/dpkg/status.d").iterdir()
               if not path.name.endswith(".md5sums")]
    matches = [record for record in records if "Package: libssl3t64\n" in record]
    if len(matches) != 1 or f"Version: {EXPECTED_PACKAGE}\n" not in matches[0]:
        raise RuntimeError("Unexpected OpenSSL Debian package inventory")


def loaded_version():
    # CPython retains the legacy five-part layout: OpenSSL 3.5.7 is
    # (3, 5, 0, 7, 0), not (3, 5, 7, ...). Read the loaded library's text.
    match = re.match(r"OpenSSL (\d+\.\d+\.\d+) ", ssl.OPENSSL_VERSION)
    if not match:
        raise RuntimeError(f"Unexpected loaded OpenSSL version format: {ssl.OPENSSL_VERSION}")
    return match[1]


def main():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("CPython version changed; renew the OpenSSL assessment")
    verify_package()
    runtime = loaded_version()
    if runtime != EXPECTED_RUNTIME:
        raise RuntimeError(f"Loaded OpenSSL differs from the assessed version: {runtime}")
    maps = Path("/proc/self/maps").read_text()
    for library in ("libssl.so.3", "libcrypto.so.3"):
        paths = {line.split()[-1] for line in maps.splitlines() if library in line}
        if paths != {f"/usr/lib/x86_64-linux-gnu/{library}"}:
            raise RuntimeError(f"Unexpected loaded OpenSSL library: {paths}")
    installed = {}
    for distribution in metadata.distributions():
        name = re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        if name in installed:
            raise RuntimeError(f"Duplicate distribution: {name}")
        installed[name] = distribution.version
    verify_distributions(installed)
    source_count = verify_application(Path("/var/task/app"))
    native_count = verify_native_callers([Path("/var/task"), Path("/usr/local/lib/python3.12/lib-dynload")])
    optional = verify_optional_crypto(
        [Path("/var/task")],
        [Path("/var/task"), Path("/usr/local/lib/python3.12/lib-dynload"), Path("/usr/lib/x86_64-linux-gnu")],
    )
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if not context.get_ca_certs() or context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("Default TLS certificate verification is not available")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        try:
            context.wrap_socket(udp, server_hostname="synthetic.invalid")
        except NotImplementedError:
            pass
        else:
            raise RuntimeError("Unexpected datagram support in CPython ssl")
    print(json.dumps({
        "package": EXPECTED_PACKAGE, "runtime": runtime, "assessment": ASSESSMENT,
        "inventory": "passed", "dependencies": "passed", "application": "passed", "native_callers": "passed",
        "tls_client": "passed", "datagram_rejected": "passed", "source_files": source_count,
        "native_files": native_count, "distribution_count": len(installed),
        "optional_crypto_assessment": "lambda-client-no-optional-crypto-v1", "optional_crypto": "passed",
        "optional_python_files": optional["python_files"], "optional_native_files": optional["native_files"],
    }))


if __name__ == "__main__":
    main()
