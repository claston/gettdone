(function () {
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const PROFILE_HINT_KEY = "ofxsimples_profile_hint";
  const yearNode = document.getElementById("footer-year");
  const menuToggle = document.getElementById("menu-toggle");
  const topLinks = document.getElementById("top-links");
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");

  const planNameNode = document.getElementById("plan-name");
  const planPriceNode = document.getElementById("plan-price");
  const planDetailsNode = document.getElementById("plan-details");
  const statusNode = document.getElementById("checkout-status");
  const formNode = document.getElementById("checkout-form");
  const submitBtn = document.getElementById("checkout-submit");
  const customerNameInput = document.getElementById("customer-name");
  const customerEmailInput = document.getElementById("customer-email");
  const successModal = document.getElementById("checkout-success-modal");
  const successMessageNode = document.getElementById("checkout-success-message");
  const successOkBtn = document.getElementById("checkout-success-ok");
  const orderWorkflowNode = document.getElementById("order-workflow");
  const orderIntentIdNode = document.getElementById("order-intent-id");
  const orderStatusValueNode = document.getElementById("order-status-value");
  const orderNextStepNode = document.getElementById("order-next-step");
  const orderPaymentLinkLineNode = document.getElementById("order-payment-link-line");
  const orderPaymentLinkNode = document.getElementById("order-payment-link");
  const orderRefreshBtn = document.getElementById("order-refresh-btn");

  let selectedPlan = null;
  let currentIntentId = String(new URL(window.location.href).searchParams.get("intent") || "").trim();

  if (yearNode) {
    yearNode.textContent = "(c) " + new Date().getFullYear() + " OFX Simples. Todos os direitos reservados.";
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
      topAuthPrimaryLink.innerHTML =
        '<span class="top-account-avatar">' +
        initial +
        '</span><span class="top-account-email">' +
        safe +
        '</span><span class="top-account-caret">▼</span>';
      topAuthPrimaryLink.classList.add("top-account-trigger");
      topAuthPrimaryLink.setAttribute("href", "./client-area.html");
    }
  }

  function renderLoggedOutTop() {
    if (topAuthLoginLink) topAuthLoginLink.classList.remove("hidden");
    if (topAuthPrimaryLink) {
      topAuthPrimaryLink.textContent = "Converter agora";
      topAuthPrimaryLink.classList.remove("top-account-trigger");
      topAuthPrimaryLink.setAttribute("href", "./ofx-convert.html");
    }
  }

  function prefillCheckoutIdentity(name, email) {
    if (customerNameInput && !String(customerNameInput.value || "").trim()) {
      customerNameInput.value = String(name || "").trim();
    }
    if (customerEmailInput && !String(customerEmailInput.value || "").trim()) {
      customerEmailInput.value = String(email || "").trim();
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
      const apiBase = resolveApiBase();
      const response = await fetch(`${apiBase}/auth/me?user_token=${encodeURIComponent(token)}`);
      if (!response.ok) {
        if (response.status === 401) {
          clearAuthState();
          renderLoggedOutTop();
        }
        return;
      }
      const payload = await response.json().catch(function () {
        return {};
      });
      const name = String(payload.name || "").trim();
      const email = String(payload.email || "").trim();
      prefillCheckoutIdentity(name, email);
      if (email) {
        setProfileHint(email);
        renderLoggedInTop(email);
      }
    } catch (_error) {
      // Keep optimistic state.
    }
  }

  function formatPriceBRL(priceCents) {
    const amount = Number(priceCents || 0) / 100;
    return amount.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function setStatus(text, isError) {
    if (!statusNode) return;
    statusNode.textContent = text || "";
    statusNode.className = "status" + (text ? (isError ? " error" : " ok") : "");
  }

  function openSuccessModal(message) {
    if (!successModal || !successMessageNode) return;
    successMessageNode.textContent = String(message || "").trim();
    successModal.classList.remove("hidden");
  }

  function closeSuccessModal() {
    if (!successModal) return;
    successModal.classList.add("hidden");
  }

  function setCheckoutReadOnly(enabled) {
    if (!formNode || !submitBtn) return;
    const fields = formNode.querySelectorAll("input, textarea, button");
    fields.forEach(function (field) {
      if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLButtonElement) {
        field.disabled = enabled;
      }
    });
    formNode.classList.toggle("is-read-only", enabled);
  }

  function setWorkflowVisible(visible) {
    if (!orderWorkflowNode) return;
    orderWorkflowNode.classList.toggle("hidden", !visible);
  }

  function mapStatusLabel(status) {
    if (status === "REQUESTED" || status === "pending") return "Solicitado";
    if (status === "AWAITING_PAYMENT") return "Aguardando pagamento";
    if (status === "RELEASED_FOR_USE") return "Liberado para uso";
    return status || "-";
  }

  function mapNextStepLabel(nextStep) {
    if (nextStep === "SEND_PAYMENT_LINK") return "Enviar link de pagamento";
    if (nextStep === "WAIT_FOR_PAYMENT") return "Aguardar pagamento";
    if (nextStep === "READY_TO_USE") return "Plano liberado para uso";
    return "Acompanhar pedido";
  }

  function renderOrderWorkflow(data) {
    if (!orderIntentIdNode || !orderStatusValueNode || !orderNextStepNode || !orderPaymentLinkLineNode || !orderPaymentLinkNode) {
      return;
    }
    orderIntentIdNode.textContent = String(data.intent_id || currentIntentId || "-");
    orderStatusValueNode.textContent = mapStatusLabel(String(data.status || ""));
    orderNextStepNode.textContent = mapNextStepLabel(String(data.next_step || ""));

    const paymentLink = String(data.payment_link || "").trim();
    if (paymentLink) {
      orderPaymentLinkNode.setAttribute("href", paymentLink);
      orderPaymentLinkLineNode.classList.remove("hidden");
    } else {
      orderPaymentLinkNode.setAttribute("href", "#");
      orderPaymentLinkLineNode.classList.add("hidden");
    }
  }

  function renderPlan(plan) {
    selectedPlan = plan || null;
    if (!planNameNode || !planPriceNode || !planDetailsNode) return;
    if (!plan) {
      planNameNode.textContent = "Plano indisponível";
      planPriceNode.textContent = "Consulte suporte";
      planDetailsNode.innerHTML = "<li>Tente novamente em instantes.</li>";
      return;
    }
    planNameNode.textContent = plan.name;
    planPriceNode.textContent = `${formatPriceBRL(plan.price_cents)}/mes`;
    planDetailsNode.innerHTML = [
      `<li>${Number(plan.quota_limit || 0)} páginas por mês</li>`,
      `<li>Até ${Number(plan.max_pages_per_file || 0)} páginas por arquivo</li>`,
      `<li>Tamanho máximo: ${Math.round(Number(plan.max_upload_size_bytes || 0) / (1024 * 1024))} MB por arquivo</li>`,
      "<li>Ativação manual apos pagamento Pix</li>",
    ].join("");
  }

  async function loadPlanCatalog() {
    const url = new URL(window.location.href);
    const requestedCode = String(url.searchParams.get("plan") || "").trim().toLowerCase();
    try {
      const apiBase = resolveApiBase();
      const response = await fetch(`${apiBase}/plans`);
      if (!response.ok) throw new Error("plan-catalog-unavailable");
      const payload = await response.json().catch(function () {
        return {};
      });
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        renderPlan(null);
        return;
      }
      items.sort(function (a, b) {
        return Number(a.price_cents || 0) - Number(b.price_cents || 0);
      });
      const matched = items.find(function (item) {
        return String(item.code || "").toLowerCase() === requestedCode;
      });
      renderPlan(matched || items[0]);
    } catch (_error) {
      renderPlan(null);
    }
  }

  async function refreshOrderStatus() {
    if (!currentIntentId) return;
    const userToken = getUserToken();
    if (!userToken) {
      setStatus("Faça login para acompanhar o status do pedido.", true);
      return;
    }
    if (orderRefreshBtn) orderRefreshBtn.disabled = true;
    try {
      const apiBase = resolveApiBase();
      const response = await fetch(
        `${apiBase}/checkout/intents/${encodeURIComponent(currentIntentId)}?user_token=${encodeURIComponent(userToken)}`,
      );
      const body = await response.json().catch(function () {
        return {};
      });
      if (!response.ok) {
        setStatus(String(body.detail || "Não foi possível atualizar o status do pedido."), true);
        return;
      }
      renderOrderWorkflow(body);
      if (String(body.status || "") === "RELEASED_FOR_USE") {
        setCheckoutReadOnly(false);
        setWorkflowVisible(false);
        currentIntentId = "";
        setStatus("Plano liberado para uso. Checkout pronto para novo pedido.", false);
      } else {
        setStatus("Status atualizado.", false);
      }
    } catch (_error) {
      setStatus("Falha de rede ao atualizar o status do pedido.", true);
    } finally {
      if (orderRefreshBtn) orderRefreshBtn.disabled = false;
    }
  }

  function enterOrderReviewMode() {
    if (!currentIntentId) return;
    setCheckoutReadOnly(true);
    setWorkflowVisible(true);
    if (orderIntentIdNode) orderIntentIdNode.textContent = currentIntentId;
    void refreshOrderStatus();
  }

  function redirectToClientAreaTracking() {
    if (!currentIntentId) {
      window.location.href = "./client-area.html#order-status";
      return;
    }
    const target =
      "./client-area.html?checkout_intent=" + encodeURIComponent(currentIntentId) + "#order-status";
    window.location.href = target;
  }

  async function handleCheckoutSubmit(event) {
    event.preventDefault();
    if (!formNode || !selectedPlan || !submitBtn) return;
    const userToken = getUserToken();
    if (!userToken) {
      setStatus("Faça login para contratar um plano.", true);
      return;
    }

    const formData = new FormData(formNode);
    const payload = {
      user_token: userToken,
      plan_code: String(selectedPlan.code || "").toLowerCase(),
      name: String(formData.get("name") || "").trim(),
      email: String(formData.get("email") || "").trim(),
      whatsapp: String(formData.get("whatsapp") || "").trim(),
      document: String(formData.get("document") || "").trim() || null,
      notes: String(formData.get("notes") || "").trim() || null,
      accepted_terms: !!document.getElementById("accepted-terms")?.checked,
    };

    if (!payload.accepted_terms) {
      setStatus("Aceite os termos de contato para continuar.", true);
      return;
    }
    if (!payload.document) {
      setStatus("Informe CPF/CNPJ para emitir a cobrança.", true);
      return;
    }

    submitBtn.disabled = true;
    setStatus("Enviando seu pedido...", false);
    try {
      const apiBase = resolveApiBase();
      const response = await fetch(`${apiBase}/checkout/intents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json().catch(function () {
        return {};
      });
      if (!response.ok) {
        setStatus(String(body.detail || "Não foi possível concluir seu pedido."), true);
        return;
      }
      const message = String(body.message || "Pedido recebido com sucesso.");
      const protocol = String(body.intent_id || "");
      currentIntentId = protocol;
      setStatus("", false);
      openSuccessModal(`${message} Protocolo: ${protocol}`);
      formNode.reset();
    } catch (_error) {
      setStatus("Falha de rede ao enviar o pedido. Tente novamente.", true);
    } finally {
      submitBtn.disabled = false;
    }
  }

  if (menuToggle && topLinks) {
    menuToggle.addEventListener("click", function () {
      const open = topLinks.classList.toggle("is-open");
      menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  if (formNode) {
    formNode.addEventListener("submit", function (event) {
      void handleCheckoutSubmit(event);
    });
  }

  if (orderRefreshBtn) {
    orderRefreshBtn.addEventListener("click", function () {
      void refreshOrderStatus();
    });
  }

  if (successOkBtn) {
    successOkBtn.addEventListener("click", function () {
      closeSuccessModal();
      redirectToClientAreaTracking();
    });
  }
  if (successModal) {
    successModal.addEventListener("click", function (event) {
      const target = event.target;
      if (target instanceof HTMLElement && target.dataset.closeModal === "true") {
        closeSuccessModal();
        redirectToClientAreaTracking();
      }
    });
  }

  void loadPlanCatalog();
  void syncTopAuthBySession();
  if (currentIntentId) {
    enterOrderReviewMode();
  } else {
    setWorkflowVisible(false);
  }
})();
