(function () {
  const form = document.getElementById("login-form");
  const statusMsg = document.getElementById("status-msg");
  const signupLink = document.getElementById("signup-link");
  const googleLoginBtn = document.getElementById("google-login-btn");
  const USER_TOKEN_KEY = "ofxsimples_user_token";

  function getStoredUserToken() {
    return String(localStorage.getItem(USER_TOKEN_KEY) || "").trim();
  }

  function storeUserToken(token) {
    localStorage.setItem(USER_TOKEN_KEY, token);
  }

  function clearUserToken() {
    localStorage.removeItem(USER_TOKEN_KEY);
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
    if (!next.startsWith("/")) return "/client-area.html";
    return next;
  }

  function shouldForceAuth() {
    const params = new URLSearchParams(window.location.search);
    const raw = String(params.get("force_auth") || "").trim().toLowerCase();
    return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
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
    if (shouldForceAuth()) {
      clearUserToken();
      return;
    }
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

  async function postLogin(payload) {
    const response = await fetch(`${apiBase}/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Falha no login.");
    return data;
  }

  if (signupLink) {
    signupLink.href = `./signup.html?next=${encodeURIComponent(getNextPath())}`;
  }

  if (form) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = document.getElementById("email");
      const password = document.getElementById("password");
      if (!(email instanceof HTMLInputElement) || !(password instanceof HTMLInputElement)) return;
      try {
        setStatus("Validando acesso...", null);
        const payload = await postLogin({ email: email.value, password: password.value });
        storeUserToken(String(payload.user_token || ""));
        setStatus("Login realizado com sucesso.", "success");
        window.location.href = getNextPath();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Falha no login.", "error");
      }
    });
  }

  if (googleLoginBtn) {
    googleLoginBtn.addEventListener("click", () => {
      const next = encodeURIComponent(getNextPath());
      window.location.href = `${apiBase}/auth/google/start?next=${next}`;
    });
  }

  void bootstrapExistingSession();
})();
