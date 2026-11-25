(function () {
  "use strict";

  const form = document.getElementById("login-form");
  const statusMsg = document.getElementById("status-msg");
  const signupLink = document.getElementById("signup-link");
  const googleLoginBtn = document.getElementById("google-login-btn");
  const PENDING_EMAIL_KEY = "ofxsimples_pending_verification_email";
  const session = window.OfxSession;

  function setStatus(message, kind) {
    if (!statusMsg) return;
    statusMsg.textContent = message || "";
    statusMsg.className = "status";
    if (kind) statusMsg.classList.add(kind);
  }

  function getNextPath() {
    const params = new URLSearchParams(window.location.search);
    const next = String(params.get("next") || "").trim();
    if (!next.startsWith("/") || /[\\\x00-\x20\x7f]/.test(next)) return "/client-area.html";
    try {
      const destination = new URL(next, window.location.origin);
      if (destination.origin !== window.location.origin) return "/client-area.html";
      return destination.pathname + destination.search + destination.hash;
    } catch (_error) {
      return "/client-area.html";
    }
  }

  function shouldForceAuth() {
    const params = new URLSearchParams(window.location.search);
    const raw = String(params.get("force_auth") || "").trim().toLowerCase();
    return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
  }

  async function bootstrapExistingSession() {
    if (!session) return;
    if (shouldForceAuth()) {
      await session.logout();
      return;
    }
    const currentUser = await session.getCurrentUser();
    if (currentUser) window.location.href = getNextPath();
  }

  async function postLogin(payload) {
    if (!session) throw new Error("Cliente de sessão indisponível.");
    const response = await session.login(payload);
    const data = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) {
      const detail = data && data.detail;
      const error = new Error(
        detail && typeof detail === "object" ? detail.message : detail || "Falha no login.",
      );
      error.code = detail && typeof detail === "object" ? String(detail.code || "") : "";
      throw error;
    }
    return data;
  }

  if (signupLink) {
    signupLink.href = `./signup.html?next=${encodeURIComponent(getNextPath())}`;
  }

  if (form) {
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const email = document.getElementById("email");
      const password = document.getElementById("password");
      if (!(email instanceof HTMLInputElement) || !(password instanceof HTMLInputElement)) return;
      try {
        setStatus("Validando acesso...", null);
        await postLogin({ email: email.value, password: password.value });
        setStatus("Login realizado com sucesso.", "success");
        window.location.href = getNextPath();
      } catch (error) {
        if (error instanceof Error && error.code === "email_verification_required") {
          sessionStorage.setItem(PENDING_EMAIL_KEY, email.value.trim());
          window.location.href = "./verificar-email.html?pending=1";
          return;
        }
        setStatus(error instanceof Error ? error.message : "Falha no login.", "error");
      }
    });
  }

  if (googleLoginBtn && session) {
    googleLoginBtn.addEventListener("click", function () {
      const next = encodeURIComponent(getNextPath());
      window.location.href = `${session.apiBase}/auth/google/start?next=${next}&flow=login`;
    });
  }

  void bootstrapExistingSession();
})();
