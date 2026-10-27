"""Exercise the official Runtime Interface Client against a local Runtime API.

Run on a Linux CI runner with Docker; no AWS credentials or production services.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXPECTED_CHECKS = ["csv", "xlsx", "ofx", "pdf", "pdfium", "psycopg"]


def smoke(image: str, output: Path, database_url: str | None = None, timeout: int = 120) -> dict:
    done = threading.Event()
    outcome = {}
    request_id = str(uuid.uuid4())

    class RuntimeAPI(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/2018-06-01/runtime/invocation/next" or outcome:
                self.send_error(410)
                return
            body = json.dumps({"contract": "worker-image-v1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Lambda-Runtime-Aws-Request-Id", request_id)
            self.send_header("Lambda-Runtime-Deadline-Ms", str(int((time.time() + timeout) * 1000)))
            self.send_header("Lambda-Runtime-Invoked-Function-Arn", "arn:aws:lambda:us-east-1:000000000000:function:synthetic")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outcome.update(path=self.path, body=body)
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
            finally:
                done.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    name = f"worker-canary-{uuid.uuid4().hex}"
    command = [
        "docker", "run", "--rm", "--name", name, "--network", "host", "--read-only", "--user", "10001:10001",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
        "-e", f"AWS_LAMBDA_RUNTIME_API=127.0.0.1:{server.server_port}", "-e", "WORKER_IMAGE_CANARY=1",
        "-e", "AWS_EC2_METADATA_DISABLED=true", "-e", "AWS_DEFAULT_REGION=us-east-1",
        "-e", "TEXTRACT_ENABLED=false", "-e", "PDF_OCR_ENABLED=false",
    ]
    if database_url:
        command.extend(["-e", f"WORKER_CANARY_DATABASE_URL={database_url}"])
    command.extend([image, "app.workers.worker_image_canary.lambda_handler"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(".log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            if not done.wait(timeout):
                raise RuntimeError("Worker Runtime API invocation timed out; see canary log.")
            output.write_text(json.dumps(outcome, indent=2), encoding="utf-8")
            expected_path = f"/2018-06-01/runtime/invocation/{request_id}/response"
            result = outcome.get("body", {})
            if outcome.get("path") != expected_path or result.get("status") != "passed" or result.get("checks") != EXPECTED_CHECKS:
                raise RuntimeError(f"Worker Runtime API invocation failed: {outcome}")
            if database_url and result.get("postgres") != "passed":
                raise RuntimeError("Worker PostgreSQL smoke test did not pass.")
            return result
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=20, check=False)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    print(json.dumps(smoke(args.image, args.output, args.database_url)))
