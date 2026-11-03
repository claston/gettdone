import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("backend_test_runner", ROOT / "scripts" / "test_backend.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("memory", [None, 256, 790, 1400])
def test_low_or_unknown_memory_uses_one_process(memory):
    assert runner.choose_workers("auto", cpu_count=8, available_mb=memory) == 1


def test_parallelism_respects_both_memory_and_cpu_budget():
    assert runner.choose_workers("auto", cpu_count=8, available_mb=2048) == 2
    assert runner.choose_workers("auto", cpu_count=2, available_mb=16384) == 2
    assert runner.choose_workers("auto", cpu_count=8, available_mb=16384) == 4
    assert runner.choose_workers("2", cpu_count=8, available_mb=790) == 2


@pytest.mark.parametrize("value", ["-1", "0", "all", "bad"])
def test_invalid_worker_configuration_fails_before_running_tests(value):
    with pytest.raises(ValueError):
        runner.choose_workers(value, cpu_count=8, available_mb=4096)


@pytest.mark.parametrize("exit_code", [0, 1, 5])
def test_runner_keeps_full_selection_records_results_and_cleans_its_temp(tmp_path, monkeypatch, exit_code):
    monkeypatch.delenv("PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(runner, "available_memory_mb", lambda: 790)
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        base = Path(command[command.index("--basetemp") + 1])
        assert base.parent == tmp_path
        (base / "nested").mkdir()
        (base / "nested" / "artifact.txt").write_text("temporary", encoding="utf-8")
        return subprocess.CompletedProcess(command, exit_code)

    report = tmp_path / "reports"
    assert runner.run(["--report-dir", str(report)], execute=execute) == exit_code
    command, options = calls[0]
    assert command[command.index("-n") + 1] == "0"
    assert "-m" not in command[3:] and "-k" not in command
    assert options["cwd"] == ROOT
    assert not Path(command[command.index("--basetemp") + 1]).exists()
    result = json.loads(next(report.glob("*/run.json")).read_text(encoding="utf-8"))
    assert result["exit_code"] == exit_code and result["workers"] == 1
    assert result["available_memory_mb"] == 790
    assert result["elapsed_seconds"] >= 0


def test_explicit_selection_and_environment_worker_override_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    seen = []
    def execute(command, **kwargs):
        seen.extend(command)
        return subprocess.CompletedProcess(command, 0)
    assert runner.run(["--report-dir", str(tmp_path / "reports"), "backend/tests/test_access_control.py", "-k", "quota"],
                      execute=execute) == 0
    assert seen[seen.index("-n") + 1] == "2"
    assert seen[-3:] == ["backend/tests/test_access_control.py", "-k", "quota"]


def test_second_suite_cannot_compete_for_the_same_machine_resources(tmp_path):
    with runner.suite_lock(tmp_path / "suite.lock"):
        with pytest.raises(RuntimeError, match="already running"):
            with runner.suite_lock(tmp_path / "suite.lock"):
                pytest.fail("second runner must not start")
    with runner.suite_lock(tmp_path / "suite.lock"):
        pass


def test_runner_refuses_to_delete_a_temp_path_outside_its_owned_directory(tmp_path):
    victim = tmp_path / "keep-me"
    victim.mkdir()
    with pytest.raises(ValueError):
        runner.cleanup_temp(victim, tmp_path)
    assert victim.exists()


def test_interrupted_run_records_exit_status_and_removes_owned_temporary_files(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))

    def interrupted(command, **kwargs):
        raise KeyboardInterrupt

    report = tmp_path / "reports"
    assert runner.run(["--report-dir", str(report)], execute=interrupted) == 130
    result = json.loads(next(report.glob("*/run.json")).read_text())
    assert result["exit_code"] == 130
    assert not list(tmp_path.glob("gettdone-pytest-run-*"))


@pytest.mark.parametrize("args", [["-nauto"], ["-n", "4"], ["--basetemp=unsafe"], ["--junitxml=lost.xml"]])
def test_pytest_arguments_cannot_override_runner_memory_or_cleanup_configuration(args):
    with pytest.raises(SystemExit) as error:
        runner.run(args, execute=lambda *a, **kw: pytest.fail("must fail before starting pytest"))
    assert error.value.code == 2
