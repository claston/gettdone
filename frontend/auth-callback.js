(function () {
  const statusMsg = document.getElementById("status-msg");
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const USER_TOKEN_COOKIE = "ofxsimples_user_token";
  const OAUTH_DEBUG_KEY = "ofxsimples_last_google_oauth_debug";
  const TOKEN_SHARED_COOKIE_ALLOWLIST = ["ofxsimples.com.br"];

  function isIpv4Host(hostname) {
    return /^\d{1,3}(\.\d{1,3}){3}$/.test(String(hostname || "").trim());
  }

  function normalizeDomainCandidate(value) {
    return String(value || "").trim().toLowerCase().replace(/^\.+/, "");
  }

  function getConfiguredSharedCookieAllowlist() {
    const configured = window.__OFX_TOKEN_SHARED_COOKIE_ALLOWLIST__;
    if (!Array.isArray(configured)) {
      return TOKEN_SHARED_COOKIE_ALLOWLIST;
    }
    const normalized = configured
      .map(function (item) {
        return normalizeDomainCandidate(item);
      })
      .filter(function (item) {
        return /^[a-z0-9.-]+$/.test(item) && item.includes(".");
      });
    if (normalized.length) {
      return normalized;
    }
    return TOKEN_SHARED_COOKIE_ALLOWLIST;
  }

  function resolveSharedCookieDomain() {
    if (window.location.protocol !== "https:") {
      return null;
    }
    const host = String(window.location.hostname || "").trim().toLowerCase();
    if (!host || host === "localhost" || isIpv4Host(host)) {
      return null;
    }
    const allowedDomains = getConfiguredSharedCookieAllowlist();
    for (const allowedDomain of allowedDomains) {
      if (host === allowedDomain || host.endsWith(`.${allowedDomain}`)) {
        return `.${allowedDomain}`;
      }
    }
    return null;
  }

  function setStatus(message, kind) {
    if (!statusMsg) {
      return;
    }
    statusMsg.textContent = message || "";
    statusMsg.className = "status";
    if (kind) {
      statusMsg.classList.add(kind);
    }
  }

  function getSafeNextPath(params) {
    const raw = String(params.get("next") || "").trim();
    if (!raw.startsWith("/") || raw.startsWith("//")) {
      return "/client-area.html";
    }
    return raw;
  }

  function storeUserToken(token) {
    const safeToken = String(token || "").trim();
    localStorage.setItem(USER_TOKEN_KEY, safeToken);
    const encodedToken = encodeURIComponent(safeToken);
    const secureAttr = window.location.protocol === "https:" ? "; Secure" : "";
    const sharedDomain = resolveSharedCookieDomain();
    document.cookie = `${USER_TOKEN_COOKIE}=${encodedToken}; Path=/; Max-Age=2592000; SameSite=Lax${secureAttr}`;
    if (sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=${encodedToken}; Path=/; Max-Age=2592000; Domain=${sharedDomain}; SameSite=Lax${secureAttr}`;
    }
  }

  function clearCallbackQuery() {
    try {
      window.history.replaceState(null, "", window.location.pathname);
    } catch (_error) {
      // no-op: browser may block history APIs in unusual contexts
    }
  }

  const params = new URLSearchParams(window.location.search);
  const userToken = String(params.get("user_token") || "").trim();
  const error = String(params.get("error") || "").trim();
  const errorDetail = String(params.get("error_detail") || "").trim();
  const nextPath = getSafeNextPath(params);

  if (userToken) {
    persistOAuthDebug({
      stage: "auth_callback_success",
      nextPath,
      hasUserToken: true,
    });
    storeUserToken(userToken);
    clearCallbackQuery();
    setStatus("Login com Google concluido. Redirecionando...", "success");
    window.setTimeout(() => {
      window.location.href = nextPath;
    }, 120);
    return;
  }

  clearCallbackQuery();
  if (error) {
    const detailSuffix = errorDetail ? ` Detalhe: ${errorDetail}` : "";
    persistOAuthDebug({
      stage: "auth_callback_error",
      nextPath,
      error,
      errorDetail,
      hasUserToken: false,
    });
    setStatus(`Nao foi possivel concluir o login com Google. Tente novamente.${detailSuffix}`, "error");
  } else {
    persistOAuthDebug({
      stage: "auth_callback_invalid_response",
      nextPath,
      hasUserToken: false,
    });
    setStatus("Resposta de autenticacao invalida.", "error");
  }

  function persistOAuthDebug(payload) {
    try {
      sessionStorage.setItem(
        OAUTH_DEBUG_KEY,
        JSON.stringify({
          ...payload,
          at: new Date().toISOString(),
        }),
      );
    } catch (_error) {
      // no-op
    }
  }

  window.setTimeout(() => {
    window.location.href = `./login.html?next=${encodeURIComponent(nextPath)}`;
  }, 1200);
})();
