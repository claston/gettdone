(function () {
  "use strict";
  const status = document.getElementById("status-msg");
  const form = document.getElementById("initial-access-form");
  const actions = document.getElementById("payment-actions");
  let token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
  window.history.replaceState(null, "", window.location.pathname);
  const local = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  const apiBase = local && window.location.port !== "8000" ? "http://127.0.0.1:8000" : window.location.origin;
  window.OfxWhatsApp?.create({ containerId: "purchase-support", linkId: "purchase-whatsapp", apiBase,
    enabled: true, message: "Olá! Preciso de ajuda com meu pedido no OFX Simples." });

  function message(text, error) {
    status.textContent = text;
    status.className = error ? "status error" : "status success";
  }

  async function post(path, body) {
    const response = await fetch(apiBase + path, { method: "POST", headers: { "Content-Type": "application/json" },
      cache: "no-store", referrerPolicy: "no-referrer", body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Não foi possível concluir. Tente novamente.");
    return data;
  }

  if (!token) {
    if (form) form.style.display = "none";
    message("Link inválido ou ausente. Solicite um novo link à equipe.", true);
    return;
  }

  if (form) {
    const button = document.getElementById("submit-btn");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (button.disabled || !token) return;
      const password = document.getElementById("password").value;
      if (password !== document.getElementById("confirm-password").value) {
        message("As senhas não coincidem. Confira e tente novamente.", true);
        return;
      }
      button.disabled = true;
      try {
        const data = await post("/auth/initial-access", { token, password, accepted_terms: document.getElementById("accepted-terms").checked });
        token = "";
        form.reset();
        form.style.display = "none";
        message(data.message, false);
      } catch (error) {
        message(error.message || "Falha de rede. Tente novamente.", true);
        button.disabled = false;
      }
    });
  }

  if (actions) {
    actions.style.display = "none";
    post("/checkout/payment-page", { token }).then((data) => {
      const url = new URL(data.payment_link);
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("Link de pagamento inválido.");
      document.getElementById("payment-summary").textContent = `Pedido ${data.order_reference} · ${data.plan_name}`;
      document.getElementById("pay-now").href = url.href;
      const input = document.getElementById("payment-url");
      input.value = data.payment_link;
      actions.hidden = false;
      actions.style.display = "";
      document.getElementById("copy-payment").addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(input.value);
          message("Link copiado. Você já pode colar onde precisar.", false);
        } catch (_error) {
          input.focus();
          input.select();
          input.setSelectionRange(0, input.value.length);
          message("Selecione e copie o link acima pelo menu do dispositivo ou com Ctrl+C.", false);
        }
      });
    }).catch((error) => {
      document.getElementById("payment-summary").textContent = "Não foi possível carregar o pagamento.";
      message(error.message || "Falha de rede. Reabra o link e tente novamente.", true);
    });
  }
})();
