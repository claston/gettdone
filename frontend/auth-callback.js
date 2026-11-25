(function () {
  "use strict";

  const statusMsg = document.getElementById("status-msg");
  const OAUTH_DEBUG_KEY = "ofxsimples_last_google_oauth_debug";
  const session = window.OfxSession;

  function setStatus(message, kind) {
    if (!statusMsg) return;
    statusMsg.textContent = message || "";
    statusMsg.className = "status";
    if (kind) statusMsg.classList.add(kind);
  }

  function getSafeNextPath(params) {
    const raw = String(params.get("next") || "").trim();
    if (!raw.startsWith("/") || /[\\\x00-\x20\x7f]/.test(raw)) return "/client-area.html";
    try {
      const destination = new URL(raw, window.location.origin);
      if (destination.origin !== window.location.origin) return "/client-area.html";
      return destination.pathname + destination.search + destination.hash;
    } catch (_error) {
      return "/client-area.html";
    }
  }

  function clearCallbackQuery() {
    try {
      window.history.replaceState(null, "", window.location.pathname);
    } catch (_error) {
      // The browser can block history APIs in unusual embedded contexts.
    }
  }

  function persistOAuthDebug(payload) {
    try {
      sessionStorage.setItem(
        OAUTH_DEBUG_KEY,
        JSON.stringify(Object.assign({}, payload, { at: new Date().toISOString() })),
      );
    } catch (_error) {
      // Diagnostic state is optional.
    }
  }

  async function completeLogin() {
    const params = new URLSearchParams(window.location.search);
    const error = String(params.get("error") || "").trim();
    const errorDetail = String(params.get("error_detail") || "").trim();
    const nextPath = getSafeNextPath(params);
    clearCallbackQuery();

    if (error) {
      const detailSuffix = errorDetail ? ` Detalhe: ${errorDetail}` : "";
      persistOAuthDebug({ stage: "auth_callback_error", nextPath, error, errorDetail });
      setStatus(`Não foi possível concluir o login com Google. Tente novamente.${detailSuffix}`, "error");
      window.setTimeout(function () {
        window.location.href = `./login.html?next=${encodeURIComponent(nextPath)}`;
      }, 1200);
      return;
    }

    const currentUser = session ? await session.getCurrentUser() : null;
    if (!currentUser) {
      persistOAuthDebug({ stage: "auth_callback_missing_session", nextPath });
      setStatus("Não foi possível validar a sessão do Google. Tente novamente.", "error");
      window.setTimeout(function () {
        window.location.href = `./login.html?next=${encodeURIComponent(nextPath)}`;
      }, 1200);
      return;
    }

    persistOAuthDebug({ stage: "auth_callback_success", nextPath });
    setStatus("Login com Google concluído. Redirecionando...", "success");
    window.setTimeout(function () {
      window.location.href = nextPath;
    }, 120);
  }

  void completeLogin();
})();
