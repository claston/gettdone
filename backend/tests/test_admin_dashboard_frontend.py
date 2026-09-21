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
    assert 'id="dashboard-top-quality-issues"' in html
    assert 'id="dashboard-canonical-capture"' in html
    assert 'id="dashboard-layouts"' in html
    assert 'id="dashboard-recent-attention"' in html
    assert 'id="dashboard-attention-export-btn"' in html
    assert 'id="dashboard-heavy-users"' in html
    assert "Baixar últimos 7 dias (CSV)" in html
    assert "Heavy users" in html
    assert 'data-admin-section="dashboard"' in html
    assert 'data-admin-section="orders"' in html
    assert 'data-admin-section="users"' in html
    assert 'data-admin-section="marketing"' in html
    assert 'data-admin-panel="marketing"' in html
    assert 'id="marketing-total"' in html
    assert 'id="marketing-contacts-list"' in html
    assert "privacy-consent.js" not in html

    assert "/admin/dashboard?" in javascript
    assert "loadDashboard" in javascript
    assert "renderDashboard" in javascript
    assert "renderDashboardLayouts" in javascript
    assert "renderDashboardCanonicalCapture" in javascript
    assert "canonical_capture_status" in javascript
    assert "clean_high_confidence_rate" in javascript
    assert '"Páginas processadas"' in javascript
    assert '"PDF (texto)"' in javascript
    assert '"OCR"' in javascript
    assert "pdf_conversions_count" in javascript
    assert "pdf_pages_count" in javascript
    assert "ocr_conversions_count" in javascript
    assert "ocr_pages_count" in javascript
    assert '"Não conversões"' in javascript
    assert "non_conversion_count" in javascript
    assert "/admin/dashboard/attention.csv?identity_type=" in javascript
    assert "downloadAttentionExport" in javascript
    assert "conversoes-atencao-ultimos-7-dias.csv" in javascript
    assert "renderDashboardHeavyUsers" in javascript
    assert "payload.heavy_users" in javascript
    assert "/admin/marketing-contacts?" in javascript
    assert "product_updates_opted_in_at" in javascript
    assert "loadMarketingContacts" in javascript
    assert "textContent" in javascript
    assert "innerHTML" not in javascript
