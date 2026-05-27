(function () {
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const USER_TOKEN_COOKIE = "ofxsimples_user_token";
  const TOKEN_SHARED_COOKIE_ALLOWLIST = ["ofxsimples.com.br"];
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
  const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
  const COLD_START_TIMEOUT_MS = 5500;

  let selectedPlan = null;
  let currentIntentId = String(new URL(window.location.href).searchParams.get("intent") || "").trim();

  function isIpv4Host(hostname) {
    return /^\d{1,3}(\.\d{1,3}){3}$/.test(String(hostname || "").trim());
  }

  function normalizeDomainCandidate(value) {
    return String(value || "").trim().toLowerCase().replace(/^\.+/, "");
  }

  function getConfiguredSharedCookieAllowlist() {
    const configured = window.__OFX_TOKEN_SHARED_COOKIE_ALLOWLIST__;
    if (!Array.isArray(configured)) {
      return TOKEN_SHARED_COOKIE_ALLOWLIST;
    }
    const normalized = configured
      .map(function (item) {
        return normalizeDomainCandidate(item);
      })
      .filter(function (item) {
        return /^[a-z0-9.-]+$/.test(item) && item.includes(".");
      });
    if (normalized.length) {
      return normalized;
    }
    return TOKEN_SHARED_COOKIE_ALLOWLIST;
  }

  function resolveLegacySharedCookieDomain() {
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

  function resolveSharedCookieDomain() {
    if (window.location.protocol !== "https:") {
      return null;
    }
    const host = String(window.location.hostname || "").trim().toLowerCase();
    if (!host || host === "localhost" || isIpv4Host(host)) {
      return null;
    }
    const allowedDomains = getConfiguredSharedCookieAllowlist();
    for (const allowedDomain of allowedDomains) {
      if (host === allowedDomain || host.endsWith(`.${allowedDomain}`)) {
        return `.${allowedDomain}`;
      }
    }
    return null;
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
    const legacySharedDomain = resolveLegacySharedCookieDomain();
    document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax${secureAttr}`;
    if (sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; Domain=${sharedDomain}; SameSite=Lax${secureAttr}`;
    }
    if (legacySharedDomain && legacySharedDomain !== sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; Domain=${legacySharedDomain}; SameSite=Lax${secureAttr}`;
    }
  }

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

  function sleep(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  function isRetryableStatus(statusCode) {
    return RETRYABLE_STATUS.has(Number(statusCode || 0));
  }

  async function fetchJsonWithRetry(url, init, options) {
    const settings = options || {};
    const maxAttempts = Math.max(1, Number(settings.attempts || 1));
    const timeoutMs = Math.max(1000, Number(settings.timeoutMs || COLD_START_TIMEOUT_MS));
    let lastError = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const timeoutHandle = controller
        ? window.setTimeout(function () {
            controller.abort();
          }, timeoutMs)
        : null;

      try {
        const response = await fetch(url, {
          ...(init || {}),
          ...(controller ? { signal: controller.signal } : {}),
        });
        const payload = await response.json().catch(function () {
          return {};
        });

        if (response.ok) {
          return { ok: true, payload: payload, status: response.status };
        }
        if (attempt < maxAttempts && isRetryableStatus(response.status)) {
          await sleep(500 * attempt);
          continue;
        }
        return { ok: false, payload: payload, status: response.status };
      } catch (error) {
        lastError = error;
        if (attempt < maxAttempts) {
          await sleep(500 * attempt);
          continue;
        }
      } finally {
        if (timeoutHandle !== null) {
          window.clearTimeout(timeoutHandle);
        }
      }
    }

    throw lastError || new Error("network-failure");
  }

  function getUserToken() {
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
    return null;
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
    clearUserTokenCookie();
  }

  function buildOptionalAuthHeaders(userToken) {
    const token = String(userToken || "").trim();
    if (!token) return null;
    return { authorization: `Bearer ${token}` };
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
    if (token) {
      renderLoggedInTop(getProfileHint());
    } else {
      renderLoggedOutTop();
    }
    try {
      const apiBase = resolveApiBase();
      const headers = buildOptionalAuthHeaders(token);
      const result = await fetchJsonWithRetry(
        `${apiBase}/auth/me`,
        {
          credentials: "include",
          ...(headers ? { headers } : {}),
        },
        { attempts: 2 },
      );
      if (!result.ok) {
        if (result.status === 401) {
          if (token) {
            clearAuthState();
          }
          renderLoggedOutTop();
        }
        return;
      }
      const payload = result.payload || {};
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
      `<li>Tamanho máximo: ${Math.round(Number(plan.max_upload_size_bytes || 0) / (1024 * 1024))} MB por arquivo</li>`,
      "<li>Ativação manual apos pagamento Pix</li>",
    ].join("");
  }

  async function loadPlanCatalog() {
    const url = new URL(window.location.href);
    const requestedCode = String(url.searchParams.get("plan") || "").trim().toLowerCase();
    setStatus("Carregando dados do plano...", false);
    try {
      const apiBase = resolveApiBase();
      const result = await fetchJsonWithRetry(`${apiBase}/plans`, undefined, { attempts: 3 });
      if (!result.ok) throw new Error("plan-catalog-unavailable");
      const payload = result.payload || {};
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        renderPlan(null);
        setStatus("Não encontramos planos disponíveis neste momento.", true);
        return;
      }
      items.sort(function (a, b) {
        return Number(a.price_cents || 0) - Number(b.price_cents || 0);
      });
      const matched = items.find(function (item) {
        return String(item.code || "").toLowerCase() === requestedCode;
      });
      renderPlan(matched || items[0]);
      setStatus("", false);
    } catch (_error) {
      renderPlan(null);
      setStatus("O servidor está iniciando. Recarregue em alguns segundos.", true);
    }
  }

  async function refreshOrderStatus() {
    if (!currentIntentId) return;
    const userToken = getUserToken();
    if (orderRefreshBtn) orderRefreshBtn.disabled = true;
    try {
      const apiBase = resolveApiBase();
      const headers = buildOptionalAuthHeaders(userToken);
      const response = await fetch(`${apiBase}/checkout/intents/${encodeURIComponent(currentIntentId)}`, {
        credentials: "include",
        ...(headers ? { headers } : {}),
      });
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

    const formData = new FormData(formNode);
    const payload = {
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
        credentials: "include",
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
