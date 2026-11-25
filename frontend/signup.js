(function () {
  "use strict";

  const form = document.getElementById("signup-form");
  const statusMsg = document.getElementById("status-msg");
  const loginLink = document.getElementById("login-link");
  const topLoginLink = document.getElementById("top-login-link");
  const googleSignupBtn = document.getElementById("google-signup-btn");
  const googleSignupDivider = document.getElementById("google-signup-divider");
  const acceptedTermsError = document.getElementById("accepted-terms-error");
  const PENDING_EMAIL_KEY = "ofxsimples_pending_verification_email";
  const PENDING_DELIVERY_KEY = "ofxsimples_pending_verification_delivery";
  const session = window.OfxSession;

  function setStatus(message, kind) {
    if (!statusMsg) return;
    statusMsg.textContent = message || "";
    statusMsg.className = "status";
    if (kind) statusMsg.classList.add(kind);
  }

  function setTermsError(message) {
    const acceptedTerms = document.getElementById("accepted-terms");
    const acceptedTermsField =
      acceptedTerms instanceof HTMLElement ? acceptedTerms.closest(".checkbox-field") : null;
    if (acceptedTermsField instanceof HTMLElement) {
      acceptedTermsField.classList.toggle("is-invalid", !!message);
    }
    if (acceptedTermsError instanceof HTMLElement) {
      acceptedTermsError.textContent = message || "";
      acceptedTermsError.classList.toggle("hidden", !message);
    }
  }

  function focusTermsField() {
    const acceptedTerms = document.getElementById("accepted-terms");
    if (acceptedTerms instanceof HTMLInputElement) {
      acceptedTerms.focus();
      acceptedTerms.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function syncGoogleSignupDivider() {
    if (!(googleSignupBtn instanceof HTMLElement) || !(googleSignupDivider instanceof HTMLElement)) return;
    googleSignupDivider.hidden = googleSignupBtn.hidden;
  }

  function getNextPath() {
    const params = new URLSearchParams(window.location.search);
    const next = String(params.get("next") || "").trim();
    if (!next.startsWith("/") || /[\\\x00-\x20\x7f]/.test(next)) return "/ofx-convert.html";
    try {
      const destination = new URL(next, window.location.origin);
      if (destination.origin !== window.location.origin) return "/ofx-convert.html";
      return destination.pathname + destination.search + destination.hash;
    } catch (_error) {
      return "/ofx-convert.html";
    }
  }

  async function bootstrapExistingSession() {
    if (!session) return;
    const currentUser = await session.getCurrentUser();
    if (currentUser) window.location.href = getNextPath();
  }

  function getReason() {
    const params = new URLSearchParams(window.location.search);
    return String(params.get("reason") || "").trim().toLowerCase();
  }

  function getPrefillValue(paramName) {
    const params = new URLSearchParams(window.location.search);
    return String(params.get(paramName) || "").trim();
  }

  async function postSignup(payload) {
    if (!session) throw new Error("Cliente de sessão indisponível.");
    const response = await fetch(`${session.apiBase}/auth/register`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) throw new Error(data.detail || "Falha no cadastro.");
    return data;
  }

  async function startCookieSession(email, password) {
    if (!session) throw new Error("Cliente de sessão indisponível.");
    const response = await session.login({ email, password });
    const payload = await response.json().catch(function () {
      return {};
    });
    if (!response.ok) {
      const detail = payload && payload.detail;
      throw new Error(
        detail && typeof detail === "object" ? detail.message : detail || "Falha ao iniciar a sessão.",
      );
    }
  }

  if (loginLink) loginLink.href = `./login.html?next=${encodeURIComponent(getNextPath())}`;
  if (topLoginLink) topLoginLink.href = `./login.html?next=${encodeURIComponent(getNextPath())}`;

  if (getReason() === "quota") {
    setStatus("Você atingiu o limite gratuito. Crie sua conta para liberar +10 conversões.", null);
  }
  if (getReason() === "google_signup_required") {
    setStatus(
      "Não encontramos uma conta para este Google. Aceite os termos e continue com Google para criar sua conta.",
      null,
    );
  }

  if (form) {
    const name = document.getElementById("name");
    const email = document.getElementById("email");
    if (name instanceof HTMLInputElement && !name.value.trim()) name.value = getPrefillValue("prefill_name");
    if (email instanceof HTMLInputElement && !email.value.trim()) email.value = getPrefillValue("prefill_email");

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const password = document.getElementById("password");
      const acceptedTerms = document.getElementById("accepted-terms");
      const productUpdatesOptIn = document.getElementById("product-updates-opt-in");
      if (
        !(name instanceof HTMLInputElement) ||
        !(email instanceof HTMLInputElement) ||
        !(password instanceof HTMLInputElement) ||
        !(acceptedTerms instanceof HTMLInputElement) ||
        !(productUpdatesOptIn instanceof HTMLInputElement)
      ) {
        return;
      }
      if (!acceptedTerms.checked) {
        setTermsError("Você precisa aceitar os Termos de Uso e a Política de Privacidade.");
        focusTermsField();
        return;
      }
      setTermsError("");
      try {
        setStatus("Criando sua conta...", null);
        const payload = await postSignup({
          name: name.value,
          email: email.value,
          password: password.value,
          accepted_terms: acceptedTerms.checked,
          product_updates_opt_in: productUpdatesOptIn.checked,
        });
        if (payload.verification_required) {
          sessionStorage.setItem(PENDING_EMAIL_KEY, String(payload.email || email.value).trim());
          sessionStorage.setItem(PENDING_DELIVERY_KEY, String(payload.email_delivery_status || ""));
          window.location.href = "./verificar-email.html?pending=1";
          return;
        }
        await startCookieSession(email.value, password.value);
        setStatus("Conta criada com sucesso.", "success");
        window.location.href = getNextPath();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Falha no cadastro.", "error");
      }
    });
  }

  if (googleSignupBtn && session) {
    googleSignupBtn.addEventListener("click", function () {
      const acceptedTerms = document.getElementById("accepted-terms");
      const productUpdatesOptIn = document.getElementById("product-updates-opt-in");
      if (!(acceptedTerms instanceof HTMLInputElement) || !(productUpdatesOptIn instanceof HTMLInputElement)) return;
      if (!acceptedTerms.checked) {
        setTermsError("Aceite os Termos de Uso e a Política de Privacidade para continuar com Google.");
        focusTermsField();
        return;
      }
      setTermsError("");
      const next = encodeURIComponent(getNextPath());
      const updates = productUpdatesOptIn.checked ? "1" : "0";
      window.location.href =
        `${session.apiBase}/auth/google/start?next=${next}&flow=signup&accepted_terms=1&product_updates_opt_in=${updates}`;
    });
  }

  const acceptedTerms = document.getElementById("accepted-terms");
  if (acceptedTerms instanceof HTMLInputElement) {
    acceptedTerms.addEventListener("change", function () {
      if (acceptedTerms.checked) setTermsError("");
    });
  }

  syncGoogleSignupDivider();
  void bootstrapExistingSession();
})();
