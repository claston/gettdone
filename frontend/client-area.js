(function () {
  const profileEmail = document.getElementById("profile-email");
  const accountEmail = document.getElementById("account-email");
  const accountAvatar = document.getElementById("account-avatar");
  const accountMenu = document.getElementById("account-menu");
  const accountMenuTrigger = document.getElementById("account-menu-trigger");
  const accountMenuPanel = document.getElementById("account-menu-panel");
  const planSummary = document.getElementById("plan-summary");
  const quotaText = document.getElementById("quota-text");
  const planText = document.getElementById("plan-text");
  const orderIntentId = document.getElementById("order-intent-id");
  const orderPlanName = document.getElementById("order-plan-name");
  const orderPlanPrice = document.getElementById("order-plan-price");
  const orderPlanPages = document.getElementById("order-plan-pages");
  const orderStatusValue = document.getElementById("order-status-value");
  const orderNextStep = document.getElementById("order-next-step");
  const orderPaymentLinkLine = document.getElementById("order-payment-link-line");
  const orderPaymentLink = document.getElementById("order-payment-link");
  const orderStatusCard = document.getElementById("order-status");
  const historyRows = document.getElementById("history-rows");
  const statusMsg = document.getElementById("status-msg");
  const logoutBtn = document.getElementById("logout-btn");
  const viewAllLink = document.getElementById("view-all-link");
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const PROFILE_HINT_KEY = "ofxsimples_profile_hint";

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

  const apiBase = resolveApiBase();

  function getUserToken() {
    const token = String(localStorage.getItem(USER_TOKEN_KEY) || "").trim();
    return token || null;
  }

  function clearUserToken() {
    localStorage.removeItem(USER_TOKEN_KEY);
    localStorage.removeItem(PROFILE_HINT_KEY);
  }

  function getProfileHint() {
    return String(localStorage.getItem(PROFILE_HINT_KEY) || "").trim() || "conta";
  }

  function setProfileHint(email) {
    const value = String(email || "").trim();
    if (value) {
      localStorage.setItem(PROFILE_HINT_KEY, value);
    }
  }

  function getInitialLabel(text) {
    const raw = String(text || "").trim();
    if (!raw) return "U";
    const first = raw[0] || "U";
    return first.toUpperCase();
  }

  function bootstrapAccountPreview() {
    const email = getProfileHint();
    if (accountEmail) {
      accountEmail.textContent = email;
    }
    if (accountAvatar) {
      accountAvatar.textContent = getInitialLabel(email);
    }
  }

  function closeAccountMenu() {
    if (!accountMenuTrigger || !accountMenuPanel) return;
    accountMenuPanel.classList.add("hidden");
    accountMenuTrigger.setAttribute("aria-expanded", "false");
  }

  function toggleAccountMenu() {
    if (!accountMenuTrigger || !accountMenuPanel) return;
    const isOpen = !accountMenuPanel.classList.contains("hidden");
    if (isOpen) {
      closeAccountMenu();
      return;
    }
    accountMenuPanel.classList.remove("hidden");
    accountMenuTrigger.setAttribute("aria-expanded", "true");
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function setStatus(message, kind) {
    statusMsg.textContent = message || "";
    statusMsg.className = "status-msg";
    if (kind) {
      statusMsg.classList.add(kind);
    }
  }

  function formatDate(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return "-";
    }

    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) {
      return escapeHtml(raw);
    }

    const parts = new Intl.DateTimeFormat("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }).formatToParts(parsed);

    const day = parts.find((part) => part.type === "day")?.value;
    const month = parts.find((part) => part.type === "month")?.value;
    const year = parts.find((part) => part.type === "year")?.value;

    if (!day || !month || !year) {
      return escapeHtml(raw);
    }

    return `${day} ${month}, ${year}`;
  }

  function normalizeStatus(status) {
    const raw = String(status || "").trim().toLowerCase();

    if (!raw) {
      return {
        label: "DESCONHECIDO",
        className: "status-processing",
      };
    }

    if (raw.includes("error") || raw.includes("erro") || raw.includes("fail")) {
      return {
        label: "ERRO",
        className: "status-error",
      };
    }

    if (raw.includes("process") || raw.includes("pending") || raw.includes("queue")) {
      return {
        label: "PROCESSANDO",
        className: "status-processing",
      };
    }

    return {
      label: "PRONTO",
      className: "status-ready",
    };
  }

  function resolveTransactions(item) {
    const possible = [
      item.transactions_count,
      item.transaction_count,
      item.total_transactions,
      item.transactions,
    ];

    for (const value of possible) {
      if (typeof value === "number" && Number.isFinite(value)) {
        return value;
      }
      if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
        return Number(value);
      }
    }

    return null;
  }

  function resolvePages(item) {
    const possible = [item.pages_count, item.page_count, item.pages];

    for (const value of possible) {
      if (typeof value === "number" && Number.isFinite(value)) {
        return value;
      }
      if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
        return Number(value);
      }
    }

    return null;
  }

  function renderRows(items) {
    if (!items || items.length === 0) {
      historyRows.innerHTML = '<tr><td colspan="5">Nenhuma conversao encontrada.</td></tr>';
      return;
    }

    historyRows.innerHTML = items
      .map((item) => {
        const normalizedStatus = normalizeStatus(item.status);
        const filename = escapeHtml(item.filename || "arquivo_sem_nome.ofx");
        const created = formatDate(item.created_at);
        const transactions = resolveTransactions(item);
        const pages = resolvePages(item);
        const txClass = typeof transactions === "number" && transactions > 0 ? "transactions-strong" : "transactions-dim";
        const pagesClass = typeof pages === "number" && pages > 0 ? "transactions-strong" : "transactions-dim";
        const txText = typeof transactions === "number" ? String(transactions) : "--";
        const pagesText = typeof pages === "number" ? String(pages) : "--";

        return `
          <tr>
            <td>
              <div class="file-cell">
                <span class="file-icon" aria-hidden="true">DOC</span>
                <span>${filename}</span>
              </div>
            </td>
            <td>${created}</td>
            <td class="${txClass}">${txText}</td>
            <td class="${pagesClass}">${pagesText}</td>
            <td>
              <span class="status-chip ${normalizedStatus.className}">${normalizedStatus.label}</span>
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function formatOrderStatus(status) {
    const normalized = String(status || "").trim().toUpperCase();
    if (normalized === "REQUESTED" || normalized === "PENDING") return "Solicitado";
    if (normalized === "AWAITING_PAYMENT") return "Aguardando pagamento";
    if (normalized === "RELEASED_FOR_USE") return "Liberado para uso";
    return normalized || "-";
  }

  function formatOrderNextStep(nextStep) {
    const normalized = String(nextStep || "").trim().toUpperCase();
    if (normalized === "SEND_PAYMENT_LINK") return "Enviar link de pagamento";
    if (normalized === "WAIT_FOR_PAYMENT") return "Aguardar pagamento";
    if (normalized === "READY_TO_USE") return "Pronto para uso";
    return normalized ? "Acompanhar pedido" : "-";
  }

  function formatPriceBRL(cents) {
    const amount = Number(cents || 0) / 100;
    return amount.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function setOrderStatusVisible(visible) {
    if (!orderStatusCard) return;
    orderStatusCard.classList.toggle("hidden", !visible);
  }

  function clearOrderStatus() {
    if (orderIntentId) orderIntentId.textContent = "-";
    if (orderPlanName) orderPlanName.textContent = "-";
    if (orderPlanPrice) orderPlanPrice.textContent = "-";
    if (orderPlanPages) orderPlanPages.textContent = "-";
    if (orderStatusValue) orderStatusValue.textContent = "-";
    if (orderNextStep) orderNextStep.textContent = "-";
    if (orderPaymentLinkLine) orderPaymentLinkLine.classList.add("hidden");
    if (orderPaymentLink) orderPaymentLink.setAttribute("href", "#");
    setOrderStatusVisible(false);
  }

  function renderOrderStatus(data, plansByCode) {
    if (!data) {
      clearOrderStatus();
      return;
    }
    setOrderStatusVisible(true);
    const orderPlanCode = String(data.plan_code || "").trim().toLowerCase();
    const catalogPlan = plansByCode && orderPlanCode ? plansByCode.get(orderPlanCode) : null;
    if (orderIntentId) orderIntentId.textContent = String(data.intent_id || "-");
    if (orderPlanName) orderPlanName.textContent = String(data.plan_name || catalogPlan?.name || "-");
    if (orderPlanPrice) orderPlanPrice.textContent = formatPriceBRL(data.price_cents);
    if (orderPlanPages) {
      orderPlanPages.textContent =
        catalogPlan && Number.isFinite(Number(catalogPlan.quota_limit))
          ? String(Number(catalogPlan.quota_limit))
          : "-";
    }
    if (orderStatusValue) orderStatusValue.textContent = formatOrderStatus(data.status);
    if (orderNextStep) orderNextStep.textContent = formatOrderNextStep(data.next_step);
    const link = String(data.payment_link || "").trim();
    if (link) {
      if (orderPaymentLink) orderPaymentLink.setAttribute("href", link);
      if (orderPaymentLinkLine) orderPaymentLinkLine.classList.remove("hidden");
    } else {
      if (orderPaymentLink) orderPaymentLink.setAttribute("href", "#");
      if (orderPaymentLinkLine) orderPaymentLinkLine.classList.add("hidden");
    }
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || "Falha ao carregar dados.");
    }
    return payload;
  }

  async function loadClientArea() {
    const token = getUserToken();
    if (!token) {
      window.location.href = "./login.html?next=%2Fclient-area.html";
      return;
    }

    try {
      const me = await fetchJson(`${apiBase}/auth/me?user_token=${encodeURIComponent(token)}`);
      const history = await fetchJson(`${apiBase}/client/conversions?user_token=${encodeURIComponent(token)}&limit=20`);
      let plansByCode = new Map();
      try {
        const plansCatalog = await fetchJson(`${apiBase}/plans`);
        const items = Array.isArray(plansCatalog.items) ? plansCatalog.items : [];
        plansByCode = new Map(
          items.map((item) => [String(item.code || "").trim().toLowerCase(), item]),
        );
      } catch (_error) {
        plansByCode = new Map();
      }
      const query = new URL(window.location.href).searchParams;
      const requestedIntentId = String(query.get("checkout_intent") || "").trim();
      let order = null;
      if (requestedIntentId) {
        try {
          order = await fetchJson(
            `${apiBase}/checkout/intents/${encodeURIComponent(requestedIntentId)}?user_token=${encodeURIComponent(token)}`,
          );
        } catch (_error) {
          order = null;
        }
      }
      if (!order) {
        try {
          order = await fetchJson(`${apiBase}/checkout/intents/latest?user_token=${encodeURIComponent(token)}`);
        } catch (_error) {
          order = null;
        }
      }

      if (profileEmail) {
        profileEmail.textContent = me.email || "-";
      }
      if (accountEmail) {
        accountEmail.textContent = me.email || "-";
      }
      if (accountAvatar) {
        accountAvatar.textContent = getInitialLabel(me.name || me.email || "U");
      }
      setProfileHint(me.email || "");
      const quotaMode = String(me.quota_mode || "conversion").toLowerCase();
      if (quotaMode === "pages") {
        quotaText.textContent = `Paginas restantes no mes: ${me.quota_remaining} / ${me.quota_limit}`;
      } else {
        quotaText.textContent = `Conversoes restantes na semana: ${me.quota_remaining} / ${me.quota_limit}`;
      }
      const maxMb = Number(me.max_upload_size_bytes || 0) / (1024 * 1024);
      const maxPages = Number(me.max_pages_per_file || 0);
      if (planText) {
        planText.textContent =
          quotaMode === "pages"
            ? `Plano pago: limite por arquivo ${maxMb.toFixed(0)} MB e ${maxPages} paginas`
            : `Plano gratuito: limite por arquivo ${maxMb.toFixed(0)} MB e ${maxPages} paginas`;
      }
      if (planSummary) {
        planSummary.textContent = quotaMode === "pages" ? "Plano pago por paginas" : "Plano gratuito por conversoes";
      }
      renderOrderStatus(order, plansByCode);
      renderRows(history.items || []);
      setStatus("Historico carregado com sucesso.", null);

      if (viewAllLink && (!history.items || history.items.length < 20)) {
        viewAllLink.style.visibility = "hidden";
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Falha ao carregar area do cliente.";
      if (message.toLowerCase().includes("invalid user token")) {
        clearUserToken();
        window.location.href = "./login.html?next=%2Fclient-area.html";
        return;
      }
      setStatus(message, "error");
      historyRows.innerHTML = '<tr><td colspan="5">Nao foi possivel carregar as conversoes.</td></tr>';
      clearOrderStatus();
    }
  }

  if (logoutBtn) {
    logoutBtn.addEventListener("click", () => {
      closeAccountMenu();
      clearUserToken();
      window.location.href = "./ofx-convert.html?logout=1";
    });
  }

  if (accountMenuTrigger) {
    accountMenuTrigger.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleAccountMenu();
    });
  }

  document.addEventListener("click", (event) => {
    if (!accountMenu || !accountMenuPanel) return;
    const target = event.target;
    if (target instanceof Node && !accountMenu.contains(target)) {
      closeAccountMenu();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAccountMenu();
    }
  });

  bootstrapAccountPreview();
  void loadClientArea();
})();
