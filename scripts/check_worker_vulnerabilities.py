"""Enforce worker scan gates with an exact, expiring exception and runtime evidence.

Raw scanner reports are never modified. Scanner execution errors must stop the
calling workflow before this policy runs. ECR input is a complete Basic scan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

BLOCKING = {"HIGH", "CRITICAL"}
SEVERITIES = BLOCKING | {"MEDIUM", "LOW", "INFORMATIONAL", "UNDEFINED", "UNKNOWN", "NONE"}
RULE_FIELDS = {"id", "package", "version", "severity", "runtime", "expires_at", "reason", "references"}


def validate_policy(policy):
    if set(policy) != {"schema_version", "exceptions"} or policy["schema_version"] != 1:
        raise ValueError("Unsupported exception policy schema")
    if not isinstance(policy["exceptions"], list):
        raise ValueError("Exception rules must be a list")
    for rule in policy["exceptions"]:
        if set(rule) != RULE_FIELDS:
            raise ValueError("Exception rule has missing or unknown fields")
        if any(not isinstance(rule[key], str) or not rule[key].strip() for key in RULE_FIELDS - {"references"}):
            raise ValueError("Exception fields must be nonempty strings")
        if rule["severity"] not in BLOCKING or any(c in rule["version"] for c in "*<>="):
            raise ValueError("Exception requires an exact version and severity")
        if not rule["references"] or not all(isinstance(ref, str) and ref.startswith("https://") for ref in rule["references"]):
            raise ValueError("Exception requires supporting references")
        date.fromisoformat(rule["expires_at"])


def finding(cve, package, version, severity, *, eligible):
    if severity not in SEVERITIES:
        raise ValueError("Missing or unsupported finding severity")
    return {"id": cve, "package": package, "version": version, "severity": severity, "eligible": eligible}


def trivy_findings(report):
    os_info = report.get("Metadata", {}).get("OS", {})
    results = report.get("Results")
    if report.get("SchemaVersion") != 2 or not isinstance(results, list) or not results:
        raise ValueError("Missing Trivy scan results")
    if os_info.get("Family") != "debian" or str(os_info.get("Name", "")).split(".")[0] != "13":
        raise ValueError("Expected the Debian 13 worker scan")
    findings = []
    for result in results:
        for item in result.get("Vulnerabilities") or []:
            findings.append(finding(item.get("VulnerabilityID"), item.get("PkgName"), item.get("InstalledVersion"),
                                    item.get("Severity"), eligible=result.get("Class") == "os-pkgs"
                                    and result.get("Type") == "debian"))
    return findings


def ecr_findings(report, *, image_digest, verified_image_digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest or ""):
        raise ValueError("ECR gate requires the immutable target image digest")
    if report.get("imageId", {}).get("imageDigest") != image_digest:
        raise ValueError("ECR scan belongs to a different image")
    if report.get("imageScanStatus", {}).get("status") != "COMPLETE" or report.get("nextToken"):
        raise ValueError("ECR scan is incomplete or not fully paginated")
    scan = report.get("imageScanFindings")
    if not isinstance(scan, dict) or "enhancedFindings" in scan:
        raise ValueError("Expected a complete ECR Basic scan; Enhanced requires its own adapter")
    counts = scan.get("findingSeverityCounts")
    items = scan.get("findings")
    if not isinstance(counts, dict) or not isinstance(items, list):
        raise ValueError("Missing ECR findings or severity counts")
    if any(key not in SEVERITIES or type(value) is not int or value < 0 for key, value in counts.items()):
        raise ValueError("Invalid ECR severity counts")
    findings = []
    for item in items:
        attributes = {a["key"]: a["value"] for a in item.get("attributes", [])}
        findings.append(finding(item.get("name"), attributes.get("package_name"), attributes.get("package_version"),
                                item.get("severity"), eligible=verified_image_digest == image_digest))
    actual_counts = Counter(item["severity"] for item in findings)
    if any(counts.get(key, 0) != actual_counts[key] for key in SEVERITIES):
        raise ValueError("ECR counts do not match detailed findings; refusing a partial scan")
    return findings


def evaluate(report, *, scanner, policy, evidence, today, image_digest="", verified_image_digest=""):
    validate_policy(policy)
    if scanner == "trivy":
        findings = trivy_findings(report)
    elif scanner == "ecr":
        findings = ecr_findings(report, image_digest=image_digest, verified_image_digest=verified_image_digest)
    else:
        raise ValueError("Unsupported scanner")
    decision = {"scanner": scanner, "evaluated_on": today.isoformat(), "accepted": [], "blocking": []}
    for item in findings:
        if item["severity"] not in BLOCKING:
            continue
        match = next((rule for rule in policy["exceptions"]
                      if item["eligible"] and all(rule[key] == item[key] for key in ("id", "package", "version", "severity"))
                      and today < date.fromisoformat(rule["expires_at"])
                      and evidence.get("package") == rule["version"] and evidence.get("runtime") == rule["runtime"]
                      and evidence.get("regression") == "passed"), None)
        item = {key: value for key, value in item.items() if key != "eligible"}
        if match:
            decision["accepted"].append({**item, **{key: match[key] for key in ("reason", "expires_at", "references")}})
        else:
            decision["blocking"].append(item)
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanner", choices=("trivy", "ecr"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=Path(".github/worker-vulnerability-exceptions.json"))
    parser.add_argument("--runtime-evidence", type=Path)
    parser.add_argument("--image-digest", default="")
    parser.add_argument("--verified-image-digest", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {}
    if args.runtime_evidence and args.runtime_evidence.is_file():
        evidence = json.loads(args.runtime_evidence.read_text(encoding="utf-8"))
    decision = evaluate(json.loads(args.report.read_text(encoding="utf-8")), scanner=args.scanner,
                        policy=json.loads(args.policy.read_text(encoding="utf-8")), evidence=evidence,
                        today=datetime.now(timezone.utc).date(), image_digest=args.image_digest,
                        verified_image_digest=args.verified_image_digest)
    output = json.dumps(decision, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"\n## {args.scanner.upper()} worker security decision\n\n"
                         f"Accepted exceptions: {len(decision['accepted'])}; blocking findings: {len(decision['blocking'])}.\n\n"
                         f"```json\n{output}\n```\n")
    return 1 if decision["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
