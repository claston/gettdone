"""Run PRO browser contracts in isolated CI suites, serially per runner."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "workspace": [("smoke_pro_workspace.py", ["--save-receipt-delay-ms", "6000"]),
                  ("smoke_pro_idle_navigation.py", []), ("smoke_pro_organization.py", [])],
    "pdf": [("smoke_pro_pdf_sync.py", []), ("smoke_pro_financial_reports.py", [])],
    "works": [("smoke_statement_works.py", [])],
}


def commands(suite, url):
    return [[sys.executable, str(ROOT / "scripts" / script), "--url", url, *args]
            for script, args in SUITES[suite]]


def wait_for_server(url, *, opener=urlopen, sleeper=time.sleep, clock=time.monotonic):
    deadline = clock() + 30
    while True:
        try:
            with opener(url + "/dashboard", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if clock() >= deadline:
            raise TimeoutError("PRO server did not become ready within 30 seconds: " + url)
        sleeper(.2)


def run(suite, url, *, output=ROOT / "tmp/pro-browser-suites", execute=subprocess.run, ready=wait_for_server):
    output.mkdir(parents=True, exist_ok=True)
    ready(url)
    scenarios = []
    for command in commands(suite, url):
        name = Path(command[1]).stem
        print(f"::group::{name}", flush=True)
        started = time.perf_counter()
        try:
            result = execute(command, cwd=ROOT, check=False)
            returncode = result.returncode
        except OSError as exc:
            print(f"Unable to execute {name}: {exc}", flush=True)
            returncode = 1
        elapsed = round(time.perf_counter() - started, 3)
        scenarios.append({"name": name, "returncode": returncode, "elapsed_seconds": elapsed})
        print(f"::endgroup::\n{name}: exit={returncode}, seconds={elapsed}", flush=True)
    summary = {"suite": suite, "scenarios": scenarios}
    (output / f"suite-{suite}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return int(any(case["returncode"] != 0 for case in scenarios))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=tuple(SUITES), required=True)
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    args = parser.parse_args()
    raise SystemExit(run(args.suite, args.url.rstrip("/")))
