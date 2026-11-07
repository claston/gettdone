"""Check a disposable API over real HTTP, including its shipped frontend.

The target must be a local test listener; never sends fixtures to production.
"""
from __future__ import annotations

import argparse
import json
import time
from urllib.parse import urlsplit

import httpx


def check_http(url: str) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
        raise ValueError("Image smoke tests require a disposable HTTP listener on 127.0.0.1.")
    with httpx.Client(base_url=url, trust_env=False, timeout=20) as client:
        deadline = time.monotonic() + 40
        while True:
            try:
                response = client.get("/health")
                response.raise_for_status()
                break
            except httpx.HTTPError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        if response.json() != {"status": "ok"}:
            raise RuntimeError("Health check failed.")
        frontend = client.get("/")
        if frontend.status_code != 200 or "text/html" not in frontend.headers.get("content-type", ""):
            raise RuntimeError("The shipped frontend is unavailable.")

        # Legacy identity parameters remain supported by the public contract.
        owner = {"anonymous_fingerprint": "synthetic-runtime-image-http"}
        raw = b"date,description,amount\n2026-04-07,IMAGE CREDIT,40.00\n2026-04-07,IMAGE DEBIT,-40.00\n"
        converted = client.post("/convert", data=owner, files={"file": ("image-smoke.csv", raw, "text/csv")})
        if converted.status_code != 200:
            raise RuntimeError(f"Conversion failed: {converted.status_code} {converted.text}")
        analysis = converted.json()["analysis"]
        if analysis["transactions_total"] != 2 or sorted(row["amount"] for row in analysis["preview_transactions"]) != [-40, 40]:
            raise RuntimeError("HTTP conversion changed the synthetic movements.")
        analysis_id = analysis["analysis_id"]
        report = client.get(f"/report/{analysis_id}", params=owner)
        if report.status_code != 200 or not report.content:
            raise RuntimeError("Report download failed.")
        ofx = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": "ofx"})
        if ofx.status_code != 200 or ofx.text.count("<STMTTRN>") != 2 or "<TRNAMT>-40.00" not in ofx.text:
            raise RuntimeError("OFX download did not preserve the movements.")
        missing = client.get("/report/an_image_missing", params=owner)
        if missing.status_code != 404:
            raise RuntimeError(f"Missing report returned {missing.status_code}, expected 404.")
        # /analyze was already retired before this image change. Do not revive it.
        retired = client.post("/analyze")
        if retired.status_code != 405:
            raise RuntimeError(f"Retired analyze endpoint returned {retired.status_code}, expected 405.")
    return {"status": "passed", "http": {"health": 200, "frontend": 200, "convert": 200,
            "report": 200, "ofx": 200, "missing_report": 404, "retired_analyze": 405}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    print(json.dumps(check_http(args.url)))
