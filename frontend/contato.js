(function () {
  const form = document.getElementById("contact-form");
  const feedback = document.getElementById("contact-feedback");
  if (!form || !feedback) {
    return;
  }

  const submitButton = form.querySelector('button[type="submit"]');

  function resolveApiBase() {
    const host = window.location.hostname;
    const port = window.location.port;
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    const isDevFrontend = isLocalHost && port !== "8000";
    if (isDevFrontend) {
      return "http://127.0.0.1:8000";
    }
    if (window.location.origin && window.location.origin !== "null") {
      return window.location.origin;
    }
    return "http://127.0.0.1:8000";
  }

  function setFeedback(message, kind) {
    feedback.textContent = message || "";
    feedback.classList.remove("error", "success");
    if (kind) {
      feedback.classList.add(kind);
    }
  }

  function setSubmitting(isSubmitting) {
    if (!submitButton) {
      return;
    }
    submitButton.disabled = isSubmitting;
    submitButton.textContent = isSubmitting ? "Enviando..." : "Enviar Mensagem";
  }

  const apiBase = resolveApiBase();

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
    if (attachment) {
      formData.append("attachment", attachment);
    }

    try {
      const response = await fetch(`${apiBase}/contact`, {
        method: "POST",
        body: formData,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = String(payload.detail || "Nao foi possivel enviar a mensagem agora.");
        setFeedback(detail, "error");
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
})();
