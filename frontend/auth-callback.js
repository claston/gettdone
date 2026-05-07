(function () {
  const statusMsg = document.getElementById("status-msg");
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const USER_TOKEN_COOKIE = "ofxsimples_user_token";

  function isIpv4Host(hostname) {
    return /^\d{1,3}(\.\d{1,3}){3}$/.test(String(hostname || "").trim());
  }

  function resolveSharedCookieDomain() {
    const host = String(window.location.hostname || "").trim().toLowerCase();
    if (!host || host === "localhost" || isIpv4Host(host)) {
      return null;
    }
    const labels = host.split(".").filter(Boolean);
    if (labels.length < 2) {
      return null;
    }
    if (labels.length >= 3 && labels[labels.length - 2] === "com" && labels[labels.length - 1] === "br") {
      return `.${labels.slice(-3).join(".")}`;
    }
    return `.${labels.slice(-2).join(".")}`;
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
  const nextPath = getSafeNextPath(params);

  if (userToken) {
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
    setStatus("Nao foi possivel concluir o login com Google. Tente novamente.", "error");
  } else {
    setStatus("Resposta de autenticacao invalida.", "error");
  }

  window.setTimeout(() => {
    window.location.href = `./login.html?next=${encodeURIComponent(nextPath)}`;
  }, 1200);
})();
