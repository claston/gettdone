from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_render_blueprint_defines_free_pro_web_service_after_ci() -> None:
    blueprint = (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")

    expected_lines = (
        "  - type: web",
        "    name: ofxsimples-pro-preview",
        "    runtime: node",
        "    plan: free",
        "    rootDir: pro",
        "    buildCommand: npm ci && npm run build",
        "    startCommand: npm run start -- -H 0.0.0.0 -p $PORT",
        "    healthCheckPath: /dashboard",
        "    autoDeployTrigger: checksPass",
        "      - key: NODE_VERSION",
        "        value: 24.21.0",
    )
    assert all(line in blueprint for line in expected_lines)


def test_ci_validates_pro_before_render_autodeploy() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    expected_lines = (
        "  pro-quality:",
        "        working-directory: pro",
        "        uses: actions/setup-node@v4",
        '          node-version: "24.21.0"',
        "        run: npm ci",
        "        run: npm run test:run",
        "        run: npm run lint",
        "        run: npm run typecheck",
        "        run: npm run build",
    )
    assert all(line in workflow for line in expected_lines)
