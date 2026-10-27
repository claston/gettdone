"""The faster gate must retain every scenario and reject skipped prerequisites."""
import json
import re
import runpy
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def job(name):
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:|\Z)", workflow, re.M | re.S)
    assert match, f"Missing job {name}"
    return match[1]


def aggregate(results, monkeypatch):
    gate = job("pro-quality")
    assert "needs: [pro-checks, pro-browser]" in gate
    assert "always()" in gate
    source = gate.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    monkeypatch.setenv("PRO_RESULTS", json.dumps(results))
    exec(compile(dedent(source), "pro-quality-workflow", "exec"), {})


def test_required_gate_accepts_only_complete_success(monkeypatch):
    aggregate({key: {"result": "success"} for key in ["pro-checks", "pro-browser"]}, monkeypatch)


@pytest.mark.parametrize("key", ["pro-checks", "pro-browser"])
@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled", "", None])
def test_required_gate_rejects_failed_or_skipped_prerequisite(key, result, monkeypatch):
    results = {name: {"result": "success"} for name in ["pro-checks", "pro-browser"]}
    results[key]["result"] = result
    with pytest.raises(SystemExit):
        aggregate(results, monkeypatch)


def test_required_gate_rejects_missing_results(monkeypatch):
    with pytest.raises(SystemExit):
        aggregate({"pro-checks": {"result": "success"}}, monkeypatch)


def runner():
    return runpy.run_path(str(ROOT / "scripts/run_pro_browser_suite.py"))


def test_browser_suites_cover_all_current_scenarios_once_with_slow_receipt():
    commands = [command for suite in ["workspace", "pdf"] for command in runner()["commands"](suite, "http://local")]
    files = [Path(command[1]).name for command in commands]
    assert Counter(files) == Counter(["smoke_pro_workspace.py", "smoke_pro_idle_navigation.py",
                                     "smoke_pro_pdf_sync.py", "smoke_pro_financial_reports.py", "smoke_pro_organization.py"])
    workspace = next(command for command in commands if command[1].endswith("smoke_pro_workspace.py"))
    assert workspace[-2:] == ["--save-receipt-delay-ms", "6000"]


def test_statement_works_browser_suite_uses_real_disposable_backend():
    commands = runner()["commands"]("works", "http://local")
    assert len(commands) == 1 and commands[0][1].endswith("smoke_statement_works.py")
    assert "backend/requirements.txt" in job("pro-browser")


def test_suite_runs_serially_and_reports_failure_without_omitting_later_scenario(tmp_path):
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == ROOT
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    result = runner()["run"]("workspace", "http://local", output=tmp_path, execute=execute, ready=lambda url: None)
    assert result == 1
    assert len(calls) == 3
    summary = json.loads((tmp_path / "suite-workspace.json").read_text())
    assert [case["returncode"] for case in summary["scenarios"]] == [1, 0, 0]


def test_suite_returns_success_only_after_both_scenarios_pass(tmp_path):
    def execute(*args, **kwargs):
        return SimpleNamespace(returncode=0)

    assert runner()["run"]("pdf", "http://local", output=tmp_path, execute=execute, ready=lambda url: None) == 0


def test_pdf_suite_waits_for_server_before_starting_browser(tmp_path):
    order = []

    def execute(*args, **kwargs):
        order.append("scenario")
        return SimpleNamespace(returncode=0)

    assert runner()["run"]("pdf", "http://local", output=tmp_path, execute=execute,
                          ready=lambda url: order.append("ready")) == 0
    assert order == ["ready", "scenario", "scenario"]


def test_readiness_retries_until_success_and_times_out():
    calls, sleeps = [], []

    def open_page(url, timeout):
        calls.append((url, timeout))
        return nullcontext(SimpleNamespace(status=503 if len(calls) == 1 else 200))

    runner()["wait_for_server"]("http://local", opener=open_page, sleeper=sleeps.append)
    assert calls == [("http://local/dashboard", 2)] * 2 and sleeps == [.2]
    ticks = iter([0, 31])
    with pytest.raises(TimeoutError):
        runner()["wait_for_server"]("http://local", opener=lambda *args, **kw: nullcontext(SimpleNamespace(status=503)),
                                    clock=lambda: next(ticks))


def test_workflow_preserves_validation_and_isolates_browser_resources():
    checks, browser = job("pro-checks"), job("pro-browser")
    assert all(command in checks for command in ["npm run test:run", "npm run lint", "npm run typecheck"])
    assert "npm run build" in browser and "NEXT_PUBLIC_API_URL: /" in browser
    assert "suite: [workspace, pdf, works]" in browser and "fail-fast: false" in browser
    assert "run_pro_browser_suite.py" in browser and "--suite" in browser
    assert "playwright install-deps chromium" in browser
    assert "playwright install --only-shell chromium" in browser
    assert "requirements-pro-browser.txt" in browser
    assert "if: matrix.suite == 'works'" in browser and "-r backend/requirements.txt" in browser
    assert 'trap ' in browser and 'kill "$pro_pid"' in browser
    assert "if: always()" in browser and "matrix.suite" in browser


def test_browser_requirements_use_same_geometry_and_fixture_versions_as_backend():
    def pins(path):
        return dict(re.findall(r"^([\w-]+)==([^\s]+)$", path.read_text(), re.M))

    browser = pins(ROOT / "scripts/requirements-pro-browser.txt")
    backend = pins(ROOT / "backend/requirements.txt")
    assert set(browser) == {"playwright", "pytest", "pypdf", "pypdfium2"}
    assert all(browser[key] == backend[key] for key in ["pytest", "pypdf", "pypdfium2"])
