from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

AUTHENTICATED_SCRIPTS = (
    "auth-callback.js",
    "checkout.js",
    "client-area.js",
    "contato.js",
    "convert.js",
    "index.js",
    "login.js",
    "ofx-convert.js",
    "ofx-landing.js",
    "planos.js",
    "signup.js",
)

SESSION_PAGES = (
    "auth-callback.html",
    "checkout.html",
    "client-area.html",
    "conciliador.html",
    "contato.html",
    "convert.html",
    "converter-pdf-para-excel.html",
    "converter-pdf-para-ofx.html",
    "index.html",
    "login.html",
    "ofx-convert.html",
    "ofx-landing.html",
    "planos.html",
    "signup.html",
    "blog/index.html",
    "blog/7-erros-comuns-na-conciliacao-bancaria/index.html",
    "blog/checklist-fechamento-financeiro-com-ofx/index.html",
    "blog/como-validar-ofx-antes-de-importar-no-erp/index.html",
    "blog/extrato-caixa-como-consultar-converter/index.html",
    "blog/o-que-e-ofx-e-como-usar/index.html",
)


def test_authenticated_frontend_scripts_do_not_persist_or_send_bearer_tokens() -> None:
    for relative_path in AUTHENTICATED_SCRIPTS:
        source = (FRONTEND_DIR / relative_path).read_text(encoding="utf-8")
        assert "ofxsimples_user_token" not in source, relative_path
        assert "USER_TOKEN_KEY" not in source, relative_path
        assert "USER_TOKEN_COOKIE" not in source, relative_path
        assert "authorization: `Bearer ${token}`" not in source, relative_path
        assert "user_token=" not in source, relative_path


def test_session_pages_load_cookie_session_client_before_page_script() -> None:
    for relative_path in SESSION_PAGES:
        source = (FRONTEND_DIR / relative_path).read_text(encoding="utf-8")
        session_index = source.find('/auth-session.js')
        assert session_index >= 0, relative_path

        page_script_name = Path(relative_path).stem + ".js"
        if relative_path.startswith("blog/") or relative_path == "index.html":
            page_script_name = "ofx-landing.js"
        elif relative_path in {"converter-pdf-para-excel.html", "converter-pdf-para-ofx.html"}:
            page_script_name = "ofx-convert.js"
        elif relative_path == "conciliador.html":
            page_script_name = "index.js"
        page_script_index = source.find(page_script_name)
        assert page_script_index > session_index, relative_path


def test_session_client_only_reads_legacy_token_for_one_way_migration() -> None:
    source = (FRONTEND_DIR / "auth-session.js").read_text(encoding="utf-8")

    assert "/auth/session/migrate" in source
    assert "/auth/session/refresh" in source
    assert 'credentials: "include"' in source
    assert "localStorage.setItem" not in source
    assert source.count("localStorage.getItem") == 1
    assert "localStorage.removeItem" in source


def test_frontend_never_writes_new_data_to_local_storage() -> None:
    for path in FRONTEND_DIR.rglob("*"):
        if path.suffix.lower() not in {".html", ".js"}:
            continue
        source = path.read_text(encoding="utf-8-sig")
        assert "localStorage.setItem" not in source, path.relative_to(FRONTEND_DIR)


def test_admin_renders_api_data_without_html_injection_sinks() -> None:
    source = (FRONTEND_DIR / "admin.js").read_text(encoding="utf-8")

    assert ".innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "document.write" not in source
    assert ".textContent" in source
    assert ".replaceChildren" in source


def test_google_callback_frontend_never_reads_a_credential_from_url() -> None:
    source = (FRONTEND_DIR / "auth-callback.js").read_text(encoding="utf-8")

    assert 'params.get("user_token")' not in source
    assert "session.getCurrentUser" in source


def test_customer_payment_links_are_restricted_to_http_urls() -> None:
    for relative_path in ("checkout.js", "client-area.js"):
        source = (FRONTEND_DIR / relative_path).read_text(encoding="utf-8")
        assert "function safePaymentLink" in source, relative_path
        assert 'url.protocol === "https:" || url.protocol === "http:"' in source, relative_path
