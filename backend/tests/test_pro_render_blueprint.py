from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_render_blueprint_defines_free_pro_web_service_after_ci() -> None:
    blueprint = yaml.safe_load((REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8"))

    service = next(item for item in blueprint["services"] if item["name"] == "ofxsimples-pro-preview")

    assert service["type"] == "web"
    assert service["runtime"] == "node"
    assert service["plan"] == "free"
    assert service["rootDir"] == "pro"
    assert service["buildCommand"] == "npm ci && npm run build"
    assert service["startCommand"] == "npm run start -- -H 0.0.0.0 -p $PORT"
    assert service["healthCheckPath"] == "/dashboard"
    assert service["autoDeployTrigger"] == "checksPass"
    assert {item["key"]: item.get("value") for item in service["envVars"]}["NODE_VERSION"] == "24.21.0"


def test_ci_validates_pro_before_render_autodeploy() -> None:
    workflow = yaml.safe_load((REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

    job = workflow["jobs"]["pro-quality"]
    assert job["defaults"]["run"]["working-directory"] == "pro"
    assert job["steps"][1]["uses"] == "actions/setup-node@v4"
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert commands == [
        "npm ci",
        "npm run test:run",
        "npm run lint",
        "npm run typecheck",
        "npm run build",
    ]
