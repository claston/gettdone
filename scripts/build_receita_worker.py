"""Compile, test and package the pinned official Java engine without Docker."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd):
    subprocess.run(list(args), cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="tmp/receita-worker")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "receita-worker/upstream.json").read_text(encoding="utf-8"))
    maven = "mvn.cmd" if os.name == "nt" else "mvn"
    with tempfile.TemporaryDirectory(prefix="receita-shadow-build-") as temporary:
        build = Path(temporary)
        source = build / "official"
        run("git", "clone", "--no-checkout", manifest["repository"], str(source), cwd=build)
        run("git", "checkout", "--detach", manifest["commit"], cwd=source)
        (source / "calculadora/db").mkdir(parents=True)
        run(maven, "-B", "-ntp", "flyway:migrate", "-Dflyway.configFiles=flyway/flyway-pro.conf", cwd=source)
        database = source / "calculadora/db/calculadora-pro.db"
        run(maven, "-B", "-ntp", "install", "-Dspring-boot.repackage.skip=true", cwd=source)
        shutil.copytree(source / "target/surefire-reports", output / "official-tests", dirs_exist_ok=True)
        adapter = ROOT / "receita-worker"
        run(maven, "-B", "-ntp", "clean", "verify", "dependency:copy-dependencies", "-DincludeScope=runtime",
            "-DoutputDirectory=target/dependency", "-Dreceita.database=" + str(database), cwd=adapter)
        shutil.copytree(adapter / "target/surefire-reports", output / "adapter-tests", dirs_exist_ok=True)
        package = build / "package"
        (package / "lib").mkdir(parents=True)
        (package / "calculadora/db").mkdir(parents=True)
        for jar in (adapter / "target/dependency").glob("*.jar"):
            shutil.copy2(jar, package / "lib" / jar.name)
        shutil.copy2(adapter / "target/receita-shadow-worker-1.0.0.jar", package / "lib/worker.jar")
        shutil.copy2(database, package / "calculadora/db/calculadora-pro.db")
        shutil.copy2(source / "LICENSE", package / "RECEITA-LICENSE.txt")
        manifest["database_sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
        (package / "upstream.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        files = [p for p in package.rglob("*") if p.is_file()]
        size = sum(p.stat().st_size for p in files)
        if size >= 250 * 1024 * 1024:
            raise SystemExit(f"Lambda ZIP exceeds 250 MiB uncompressed: {size}")
        archive = output / "receita-shadow.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in files:
                bundle.write(path, path.relative_to(package))
        extracted = build / "smoke"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extracted)
        run("java", "-cp", str(extracted / "lib/*"), "br.com.ofxsimples.shadow.PackagedSmoke", cwd=extracted)
        if hashlib.sha256((extracted / "calculadora/db/calculadora-pro.db").read_bytes()).hexdigest() != manifest["database_sha256"]:
            raise SystemExit("Official database was modified")
        application_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        evidence = {**manifest, "application_commit": application_commit, "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "uncompressed_bytes": size, "packaged_smoke": "passed", "upstream_tests": "passed", "adapter_tests": "passed"}
        (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
