"""Prefer the existing Ubuntu HTTPS mirror without removing signed fallbacks."""
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIRRORS = ("http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
           "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
           "https://security.ubuntu.com/ubuntu/\tpriority:3\n")


def helper():
    return runpy.run_path(str(ROOT / "scripts/configure_pro_apt_mirror.py"))


def test_prefer_existing_https_archive_and_keep_every_fallback():
    configure = helper()["prefer_ubuntu_archive"]
    result = configure(MIRRORS)
    assert result.splitlines() == ["http://azure.archive.ubuntu.com/ubuntu/\tpriority:2",
        "https://archive.ubuntu.com/ubuntu/\tpriority:1", "https://security.ubuntu.com/ubuntu/\tpriority:3"]
    assert configure(result) == result


@pytest.mark.parametrize("text", ["", "https://example.invalid/ubuntu/ priority:1\n",
    "http://azure.archive.ubuntu.com/ubuntu/ priority:1\n",
    "https://archive.ubuntu.com/ubuntu/ priority:20\nhttp://azure.archive.ubuntu.com/ubuntu/ priority:1\n"])
def test_unknown_runner_configuration_is_preserved(text):
    assert helper()["prefer_ubuntu_archive"](text) == text


def test_configure_only_existing_mirror_file(tmp_path):
    file = tmp_path / "mirrors.txt"
    configure = helper()["configure"]
    assert configure(file) is False
    file.write_text(MIRRORS)
    assert configure(file) is True
    assert "https://security.ubuntu.com/ubuntu/\tpriority:3" in file.read_text()
    assert configure(file) is False


def test_workflow_installs_all_os_dependencies_before_chromium():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert workflow.index("configure_pro_apt_mirror.py") < workflow.index("playwright install-deps chromium")
    assert workflow.index("playwright install-deps chromium") < workflow.index("playwright install --only-shell chromium")
