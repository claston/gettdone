(function () {
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const PROFILE_HINT_KEY = "ofxsimples_profile_hint";
  const form = document.getElementById("contact-form");
  const feedback = document.getElementById("contact-feedback");
  if (!form || !feedback) return;

  const submitButton = form.querySelector('button[type="submit"]');

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

  function getUserToken() {
    const token = String(localStorage.getItem(USER_TOKEN_KEY) || "").trim();
    return token || null;
  }

  function getProfileHint() {
    return String(localStorage.getItem(PROFILE_HINT_KEY) || "").trim() || "conta";
  }

  function setProfileHint(email) {
    const value = String(email || "").trim();
    if (value) localStorage.setItem(PROFILE_HINT_KEY, value);
  }

  function clearAuthState() {
    localStorage.removeItem(USER_TOKEN_KEY);
    localStorage.removeItem(PROFILE_HINT_KEY);
  }

  function renderLoggedInTop(email) {
    if (topAuthLoginLink) topAuthLoginLink.classList.add("hidden");
    if (topAuthPrimaryLink) {
      const safe = String(email || "conta").trim() || "conta";
      const initial = safe.charAt(0).toUpperCase();
      topAuthPrimaryLink.innerHTML = `<span class="top-account-avatar">${initial}</span><span class="top-account-email">${safe}</span><span class="top-account-caret">▾</span>`;
      topAuthPrimaryLink.classList.add("top-account-trigger");
      topAuthPrimaryLink.setAttribute("href", "./client-area.html");
    }
  }

  function renderLoggedOutTop() {
    if (topAuthLoginLink) {
      topAuthLoginLink.classList.remove("hidden");
      topAuthLoginLink.setAttribute("href", "./login.html?next=%2Fofx-convert.html");
    }
    if (topAuthPrimaryLink) {
      topAuthPrimaryLink.textContent = "Converter agora";
      topAuthPrimaryLink.classList.remove("top-account-trigger");
      topAuthPrimaryLink.setAttribute("href", "./ofx-convert.html");
    }
  }

  async function syncTopAuthBySession() {
    const token = getUserToken();
    if (!token) {
      renderLoggedOutTop();
      return;
    }

    renderLoggedInTop(getProfileHint());

    try {
      const response = await fetch(`${apiBase}/auth/me`, {
        headers: { authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        if (response.status === 401) {
          clearAuthState();
          renderLoggedOutTop();
        }
        return;
      }
      const payload = await response.json().catch(() => ({}));
      const email = String(payload.email || "").trim();
      if (email) {
        setProfileHint(email);
        renderLoggedInTop(email);
      }
    } catch (_error) {
      // Keep optimistic state.
    }
  }

  function setFeedback(message, kind) {
    feedback.textContent = message || "";
    feedback.classList.remove("error", "success");
    if (kind) feedback.classList.add(kind);
  }

  function setSubmitting(isSubmitting) {
    if (!submitButton) return;
    submitButton.disabled = isSubmitting;
    submitButton.textContent = isSubmitting ? "Enviando..." : "Enviar Mensagem";
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const name = String(document.getElementById("name").value || "").trim();
    const email = String(document.getElementById("email").value || "").trim();
    const subject = String(document.getElementById("subject").value || "").trim();
    const message = String(document.getElementById("message").value || "").trim();
    const attachmentInput = document.getElementById("attachment");
    const attachment = attachmentInput && attachmentInput.files ? attachmentInput.files[0] : null;

    if (!name || !email || !subject || !message) {
      setFeedback("Preencha nome, e-mail, assunto e mensagem.", "error");
      return;
    }

    setSubmitting(true);
    setFeedback("Enviando sua mensagem...", null);

    const formData = new FormData();
    formData.append("name", name);
    formData.append("email", email);
    formData.append("subject", subject);
    formData.append("message", message);
    if (attachment) formData.append("attachment", attachment);

    try {
      const response = await fetch(`${apiBase}/contact`, { method: "POST", body: formData });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setFeedback(String(payload.detail || "Nao foi possivel enviar a mensagem agora."), "error");
        return;
      }
      if (payload.delivery_mode === "dry_run") {
        setFeedback("Mensagem registrada em modo teste. Ative o Resend para envio real por e-mail.", "success");
      } else {
        setFeedback("Mensagem enviada com sucesso. Nossa equipe vai responder no seu e-mail.", "success");
      }
      form.reset();
    } catch (_error) {
      setFeedback("Falha de rede ao enviar a mensagem. Tente novamente em instantes.", "error");
    } finally {
      setSubmitting(false);
    }
  });

  void syncTopAuthBySession();
})();
