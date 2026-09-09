from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def test_admin_dashboard_frontend_exposes_summary_filters_and_attention_sections() -> None:
    html = (FRONTEND_DIR / "admin.html").read_text(encoding="utf-8")
    javascript = (FRONTEND_DIR / "admin.js").read_text(encoding="utf-8")

    assert 'id="admin-dashboard-card"' in html
    assert 'id="dashboard-period"' in html
    assert 'id="dashboard-identity-type"' in html
    assert 'id="dashboard-summary"' in html
    assert 'id="dashboard-daily-chart"' in html
    assert 'id="dashboard-top-errors"' in html
    assert 'id="dashboard-recent-attention"' in html
    assert 'data-admin-section="dashboard"' in html
    assert 'data-admin-section="orders"' in html
    assert 'data-admin-section="users"' in html
    assert "privacy-consent.js" not in html

    assert "/admin/dashboard?" in javascript
    assert "loadDashboard" in javascript
    assert "renderDashboard" in javascript
    assert "textContent" in javascript
    assert "innerHTML" not in javascript
