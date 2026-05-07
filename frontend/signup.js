(function () {
  const form = document.getElementById("signup-form");
  const statusMsg = document.getElementById("status-msg");
  const loginLink = document.getElementById("login-link");
  const topLoginLink = document.getElementById("top-login-link");
  const googleSignupBtn = document.getElementById("google-signup-btn");
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

  function readUserTokenCookie() {
    const entries = String(document.cookie || "").split(";");
    for (const entry of entries) {
      const [namePart, ...valueParts] = entry.split("=");
      const name = String(namePart || "").trim();
      if (name !== USER_TOKEN_COOKIE) continue;
      const rawValue = valueParts.join("=");
      const decoded = decodeURIComponent(String(rawValue || "").trim());
      if (decoded) return decoded;
    }
    return "";
  }

  function writeUserTokenCookie(token) {
    const safeToken = encodeURIComponent(String(token || "").trim());
    if (!safeToken) return;
    const secureAttr = window.location.protocol === "https:" ? "; Secure" : "";
    const sharedDomain = resolveSharedCookieDomain();
    document.cookie = `${USER_TOKEN_COOKIE}=${safeToken}; Path=/; Max-Age=2592000; SameSite=Lax${secureAttr}`;
    if (sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=${safeToken}; Path=/; Max-Age=2592000; Domain=${sharedDomain}; SameSite=Lax${secureAttr}`;
    }
  }

  function clearUserTokenCookie() {
    const secureAttr = window.location.protocol === "https:" ? "; Secure" : "";
    const sharedDomain = resolveSharedCookieDomain();
    document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax${secureAttr}`;
    if (sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; Domain=${sharedDomain}; SameSite=Lax${secureAttr}`;
    }
  }

  function getStoredUserToken() {
    const localToken = String(localStorage.getItem(USER_TOKEN_KEY) || "").trim();
    if (localToken) {
      writeUserTokenCookie(localToken);
      return localToken;
    }
    const cookieToken = readUserTokenCookie();
    if (cookieToken) {
      localStorage.setItem(USER_TOKEN_KEY, cookieToken);
      return cookieToken;
    }
    return "";
  }

  function storeUserToken(token) {
    const safeToken = String(token || "").trim();
    localStorage.setItem(USER_TOKEN_KEY, safeToken);
    writeUserTokenCookie(safeToken);
  }

  function clearUserToken() {
    localStorage.removeItem(USER_TOKEN_KEY);
    clearUserTokenCookie();
  }

  function resolveApiBase() {
    const host = window.location.hostname;
    const port = window.location.port;
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    const isDevFrontend = isLocalHost && port !== "8000";
    if (isDevFrontend) return "http://127.0.0.1:8000";
    if (window.location.origin && window.location.origin !== "null") return window.location.origin;
    return "http://127.0.0.1:8000";
  }

  const apiBase = resolveApiBase();

  function setStatus(message, kind) {
    statusMsg.textContent = message || "";
    statusMsg.className = "status";
    if (kind) statusMsg.classList.add(kind);
  }

  function getNextPath() {
    const params = new URLSearchParams(window.location.search);
    const next = String(params.get("next") || "").trim();
    if (!next.startsWith("/")) return "/ofx-convert.html";
    return next;
  }

  async function getSessionValidationState(token) {
    if (!token) return "missing";
    try {
      const response = await fetch(`${apiBase}/auth/me`, {
        headers: { authorization: `Bearer ${token}` },
      });
      if (response.ok) {
        return "valid";
      }
      if (response.status === 401) {
        return "invalid";
      }
      return "unknown";
    } catch (_error) {
      return "unknown";
    }
  }

  async function bootstrapExistingSession() {
    const existingToken = getStoredUserToken();
    if (!existingToken) return;
    const sessionState = await getSessionValidationState(existingToken);
    if (sessionState === "valid") {
      window.location.href = getNextPath();
      return;
    }
    if (sessionState === "invalid") {
      clearUserToken();
    }
  }

  function getReason() {
    const params = new URLSearchParams(window.location.search);
    return String(params.get("reason") || "").trim().toLowerCase();
  }

  async function postSignup(payload) {
    const response = await fetch(`${apiBase}/auth/register`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Falha no cadastro.");
    return data;
  }

  if (loginLink) {
    loginLink.href = `./login.html?next=${encodeURIComponent(getNextPath())}`;
  }

  if (topLoginLink) {
    topLoginLink.href = `./login.html?next=${encodeURIComponent(getNextPath())}`;
  }

  if (getReason() === "quota") {
    setStatus("Você atingiu o limite gratuito. Crie sua conta para liberar +10 conversões.", null);
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const name = document.getElementById("name");
      const email = document.getElementById("email");
      const password = document.getElementById("password");
      if (
        !(name instanceof HTMLInputElement) ||
        !(email instanceof HTMLInputElement) ||
        !(password instanceof HTMLInputElement)
      ) {
        return;
      }
      try {
        setStatus("Criando sua conta...", null);
        const payload = await postSignup({
          name: name.value,
          email: email.value,
          password: password.value,
        });
        storeUserToken(String(payload.user_token || ""));
        setStatus("Conta criada com sucesso.", "success");
        window.location.href = getNextPath();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Falha no cadastro.", "error");
      }
    });
  }

  if (googleSignupBtn) {
    googleSignupBtn.addEventListener("click", () => {
      const next = encodeURIComponent(getNextPath());
      window.location.href = `${apiBase}/auth/google/start?next=${next}`;
    });
  }

  void bootstrapExistingSession();
})();
