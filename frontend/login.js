(function () {
  const form = document.getElementById("login-form");
  const statusMsg = document.getElementById("status-msg");
  const signupLink = document.getElementById("signup-link");
  const topSignupLink = document.getElementById("top-signup-link");
  const googleLoginBtn = document.getElementById("google-login-btn");

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

  async function hasValidSession(token) {
    if (!token) return false;
    try {
      const response = await fetch(`${apiBase}/auth/me?user_token=${encodeURIComponent(token)}`);
      return response.ok;
    } catch (_error) {
      return false;
    }
  }

  async function bootstrapExistingSession() {
    const existingToken = String(localStorage.getItem("gettdone_user_token") || "").trim();
    if (!existingToken) return;
    const isValid = await hasValidSession(existingToken);
    if (isValid) {
      window.location.href = getNextPath();
      return;
    }
    localStorage.removeItem("gettdone_user_token");
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

  if (topSignupLink) {
    topSignupLink.href = `./signup.html?next=${encodeURIComponent(getNextPath())}`;
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
        localStorage.setItem("gettdone_user_token", String(payload.user_token || ""));
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
