(function () {
  "use strict";

  const requestForm = document.getElementById("forgot-password-form");
  const resetForm = document.getElementById("reset-password-form");
  const status = document.getElementById("status-msg");
  const button = document.getElementById("submit-btn");
  let token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
  if (resetForm) window.history.replaceState(null, "", window.location.pathname);

  function setStatus(message, kind) {
    status.textContent = message;
    status.className = `status ${kind || ""}`;
  }

  function apiBase() {
    const host = window.location.hostname;
    if ((host === "localhost" || host === "127.0.0.1") && window.location.port !== "8000") {
      return "http://127.0.0.1:8000";
    }
    return window.location.origin;
  }

  async function post(path, payload) {
    const response = await fetch(`${apiBase()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const fallback = response.status === 429
        ? "Muitas tentativas. Aguarde um minuto e tente novamente."
        : "Não foi possível concluir a solicitação. Tente novamente.";
      throw new Error(typeof data.detail === "string" ? data.detail : fallback);
    }
    return data;
  }

  if (requestForm) {
    requestForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (button.disabled) return;
      button.disabled = true;
      setStatus("Solicitando recuperação…");
      try {
        const data = await post("/auth/forgot-password", { email: document.getElementById("email").value.trim() });
        setStatus(data.message, "success");
        button.textContent = "Aguarde 60 segundos para reenviar";
        window.setTimeout(() => {
          button.disabled = false;
          button.textContent = "Enviar link de recuperação";
        }, 60000);
      } catch (_error) {
        setStatus("Não foi possível solicitar o link. Verifique sua conexão e tente novamente.", "error");
        button.disabled = false;
      }
    });
  }

  if (resetForm) {
    if (!token) {
      resetForm.hidden = true;
      resetForm.style.display = "none";
      setStatus("Link inválido ou ausente. Solicite um novo link de recuperação.", "error");
    }
    resetForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (button.disabled || !token) return;
      const password = document.getElementById("password").value;
      if (password !== document.getElementById("confirm-password").value) {
        setStatus("As senhas não coincidem. Confira e tente novamente.", "error");
        return;
      }
      button.disabled = true;
      setStatus("Salvando nova senha…");
      try {
        const data = await post("/auth/reset-password", { token, password });
        token = "";
        resetForm.reset();
        resetForm.hidden = true;
        resetForm.style.display = "none";
        setStatus(data.message, "success");
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Não foi possível redefinir a senha.", "error");
        button.disabled = false;
      }
    });
  }
})();
