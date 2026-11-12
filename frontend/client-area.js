(function () {
  "use strict";

  const session = window.OfxSession;
  const profileEmail = document.getElementById("profile-email");
  const accountEmail = document.getElementById("account-email");
  const accountAvatar = document.getElementById("account-avatar");
  const accountMenu = document.getElementById("account-menu");
  const accountMenuTrigger = document.getElementById("account-menu-trigger");
  const accountMenuPanel = document.getElementById("account-menu-panel");
  const planSummary = document.getElementById("plan-summary");
  const planModeSummary = document.getElementById("plan-mode-summary");
  const quotaText = document.getElementById("quota-text");
  const planText = document.getElementById("plan-text");
  const activationHintText = document.getElementById("activation-hint-text");
  const orderIntentId = document.getElementById("order-intent-id");
  const orderPlanName = document.getElementById("order-plan-name");
  const orderPlanPrice = document.getElementById("order-plan-price");
  const orderPlanPages = document.getElementById("order-plan-pages");
  const orderStatusValue = document.getElementById("order-status-value");
  const orderNextStep = document.getElementById("order-next-step");
  const orderPaymentLinkLine = document.getElementById("order-payment-link-line");
  const orderPaymentLink = document.getElementById("order-payment-link");
  const orderStatusCard = document.getElementById("order-status");
  const ordersRows = document.getElementById("orders-rows");
  const historyRows = document.getElementById("history-rows");
  const statusMsg = document.getElementById("status-msg");
  const logoutBtn = document.getElementById("logout-btn");
  const viewAllLink = document.getElementById("view-all-link");
  const TRANSIENT_OVERLAY_SELECTORS = [
    "#quota-lock-overlay",
    ".quota-lock-overlay",
    ".modal-backdrop",
    ".modal",
    ".modal-overlay",
    "[data-close-modal='true']",
  ];
  const TRANSIENT_BODY_LOCK_CLASSES = ["quota-locked", "modal-open"];
  const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
  const COLD_START_TIMEOUT_MS = 5500;
  let unlockRetryTimer = null;

  const apiBase = session ? session.apiBase : window.location.origin;
  const support = window.OfxWhatsApp?.create({
    containerId: "client-support",
    linkId: "client-whatsapp-link",
    apiBase,
  });

  function forceUnlockTransientUi() {
    if (document.body) {
      for (const className of TRANSIENT_BODY_LOCK_CLASSES) {
        document.body.classList.remove(className);
      }
      document.body.style.pointerEvents = "";
      document.body.style.overflow = "";
    }

    if (document.documentElement) {
      for (const className of TRANSIENT_BODY_LOCK_CLASSES) {
        document.documentElement.classList.remove(className);
      }
      document.documentElement.style.pointerEvents = "";
      document.documentElement.style.overflow = "";
    }

    for (const selector of TRANSIENT_OVERLAY_SELECTORS) {
      const nodes = document.querySelectorAll(selector);
      for (const node of nodes) {
        if (!(node instanceof HTMLElement)) continue;
        node.classList.remove("is-open");
        node.classList.add("hidden");
        node.style.display = "none";
        node.style.pointerEvents = "none";
      }
    }
  }

  function scheduleForceUnlockTransientUi() {
    if (unlockRetryTimer !== null) {
      window.clearTimeout(unlockRetryTimer);
    }
    unlockRetryTimer = window.setTimeout(function () {
      forceUnlockTransientUi();
      unlockRetryTimer = null;
    }, 220);
  }

  function getInitialLabel(text) {
    const raw = String(text || "").trim();
    if (!raw) return "U";
    const first = raw[0] || "U";
    return first.toUpperCase();
  }

  function bootstrapAccountPreview() {
    const email = "conta";
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

    const parts = new Intl.DateTimeFormat("pt-BR", {
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

    return `${day} ${month} ${year}`;
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
      historyRows.innerHTML = '<tr><td colspan="5">Nenhuma conversão encontrada.</td></tr>';
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

  function normalizeOrderStatus(status) {
    const normalized = String(status || "").trim().toUpperCase();
    if (normalized === "RELEASED_FOR_USE") {
      return { label: "LIBERADO", className: "status-ready" };
    }
    if (normalized === "REQUESTED" || normalized === "PENDING" || normalized === "AWAITING_PAYMENT") {
      return { label: formatOrderStatus(normalized).toUpperCase(), className: "status-processing" };
    }
    return { label: formatOrderStatus(normalized).toUpperCase(), className: "status-error" };
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

  function safePaymentLink(value) {
    try {
      const url = new URL(String(value || "").trim());
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch (_error) {
      return "";
    }
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

  function renderOrderRows(items) {
    if (!ordersRows) return;
    if (!Array.isArray(items) || items.length === 0) {
      ordersRows.innerHTML = '<tr><td colspan="5">Nenhum pedido encontrado.</td></tr>';
      return;
    }
    ordersRows.innerHTML = items
      .map((item) => {
        const statusData = normalizeOrderStatus(item.status);
        const intentId = escapeHtml(String(item.intent_id || "-"));
        const createdAt = formatDate(item.created_at);
        const planName = escapeHtml(String(item.plan_name || "-"));
        const value = formatPriceBRL(item.price_cents);
        return `
          <tr>
            <td>${intentId}</td>
            <td>${createdAt}</td>
            <td>${planName}</td>
            <td>${value}</td>
            <td><span class="status-chip ${statusData.className}">${statusData.label}</span></td>
          </tr>
        `;
      })
      .join("");
  }

  function renderOrderStatus(data, plansByCode) {
    if (!data) {
      clearOrderStatus();
      return;
    }
    const orderStatus = String(data.status || "").trim().toUpperCase();
    if (orderStatus === "RELEASED_FOR_USE") {
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
    const link = safePaymentLink(data.payment_link);
    if (link) {
      if (orderPaymentLink) orderPaymentLink.setAttribute("href", link);
      if (orderPaymentLinkLine) orderPaymentLinkLine.classList.remove("hidden");
    } else {
      if (orderPaymentLink) orderPaymentLink.setAttribute("href", "#");
      if (orderPaymentLinkLine) orderPaymentLinkLine.classList.add("hidden");
    }
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  function isRetryableStatus(statusCode) {
    return RETRYABLE_STATUS.has(Number(statusCode || 0));
  }

  async function fetchJson(url, init, options) {
    const settings = options || {};
    const maxAttempts = Math.max(1, Number(settings.attempts || 1));
    const timeoutMs = Math.max(1000, Number(settings.timeoutMs || COLD_START_TIMEOUT_MS));
    const requestInit = {
      credentials: "include",
      ...(init || {}),
    };
    let lastError = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const timeoutHandle = controller
        ? window.setTimeout(function () {
            controller.abort();
          }, timeoutMs)
        : null;

      try {
        const response = await session.request(url, {
          ...requestInit,
          ...(controller ? { signal: controller.signal } : {}),
        });
        const payload = await response.json().catch(() => ({}));
        if (response.ok) {
          return payload;
        }
        if (attempt < maxAttempts && isRetryableStatus(response.status)) {
          await sleep(500 * attempt);
          continue;
        }
        throw new Error(payload.detail || "Falha ao carregar dados.");
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

    throw (lastError instanceof Error ? lastError : new Error("Falha ao carregar dados."));
  }

  function mapPlansByCode(plansCatalog) {
    const items = Array.isArray(plansCatalog?.items) ? plansCatalog.items : [];
    return new Map(items.map((item) => [String(item.code || "").trim().toLowerCase(), item]));
  }

  async function loadOrderData(requestedIntentId) {
    const orders = [];
    const seenIntentIds = new Set();
    function pushOrder(item) {
      if (!item) return;
      const intentId = String(item.intent_id || "").trim();
      if (!intentId || seenIntentIds.has(intentId)) return;
      seenIntentIds.add(intentId);
      orders.push(item);
    }

    if (requestedIntentId) {
      try {
        const requestedOrder = await fetchJson(
          `${apiBase}/checkout/intents/${encodeURIComponent(requestedIntentId)}`,
          undefined,
          { attempts: 2 },
        );
        pushOrder(requestedOrder);
      } catch (_error) {
        // Keep loading with fallback.
      }
    }

    try {
      const latestOrder = await fetchJson(`${apiBase}/checkout/intents/latest`, undefined, { attempts: 2 });
      pushOrder(latestOrder);
    } catch (_error) {
      // Optional source for history.
    }
    return orders;
  }

  async function loadClientArea() {
    support?.update({ enabled: false });
    forceUnlockTransientUi();
    scheduleForceUnlockTransientUi();

    try {
      const query = new URL(window.location.href).searchParams;
      const requestedIntentId = String(query.get("checkout_intent") || "").trim();
      const mePromise = fetchJson(`${apiBase}/auth/me`, undefined, { attempts: 3 }).then((me) => {
        const paidPlan = me.quota_mode === "pages" && Boolean(String(me.plan_code || "").trim());
        const planName = String(me.plan_name || "").trim();
        support?.update({
          enabled: paidPlan,
          message: planName
            ? `Olá! Sou cliente do OFX Simples, tenho o plano ${planName} e preciso de suporte.`
            : "Olá! Sou cliente do OFX Simples e preciso de suporte.",
        });
        // Count entry once authentication succeeds, even if optional page data fails.
        void fetchJson(`${apiBase}/telemetry/events`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          keepalive: true,
          body: JSON.stringify({ event_type: "client_area_view", page_path: "/client-area.html" }),
        }).catch(() => {});
        return me;
      });
      const historyPromise = fetchJson(`${apiBase}/client/conversions?limit=20`, undefined, { attempts: 3 });
      const plansPromise = fetchJson(`${apiBase}/plans`, undefined, { attempts: 3 }).catch(() => ({ items: [] }));

      const [me, history] = await Promise.all([mePromise, historyPromise]);
      renderRows(history.items || []);
      if (viewAllLink && (!history.items || history.items.length < 20)) {
        viewAllLink.style.visibility = "hidden";
      }
      setStatus("", null);

      if (profileEmail) {
        profileEmail.textContent = me.email || "-";
      }
      if (accountEmail) {
        accountEmail.textContent = me.email || "-";
      }
      if (accountAvatar) {
        accountAvatar.textContent = getInitialLabel(me.name || me.email || "U");
      }
      const quotaMode = String(me.quota_mode || "conversion").toLowerCase();
      if (quotaMode === "pages") {
        quotaText.textContent = `${me.quota_remaining} / ${me.quota_limit}`;
      } else {
        quotaText.textContent = `${me.quota_remaining} / ${me.quota_limit}`;
      }
      
      
      if (planText) {
        planText.textContent =
          "Limite por arquivo: PDF com texto até 10 MB.";
      }
      if (planSummary) {
        planSummary.textContent = String(me.plan_name || "").trim() || (quotaMode === "pages" ? "Plano pago" : "Plano gratuito");
      }
      if (planModeSummary) {
        planModeSummary.textContent = quotaMode === "pages" ? "Pago por páginas" : "Gratuito por conversões";
      }
      if (activationHintText) {
        activationHintText.textContent =
          quotaMode === "pages"
            ? "Seu plano está ativo. Você já pode usar todas as funcionalidades da conta."
            : "Você ainda está no plano gratuito. Ative um plano para ampliar limites e liberar novos recursos.";
      }
      setStatus("", null);

      void Promise.all([
        plansPromise.then((plansCatalog) => mapPlansByCode(plansCatalog)),
        loadOrderData(requestedIntentId),
      ]).then(([plansByCode, orders]) => {
        renderOrderRows(orders);
        const pendingOrder =
          Array.isArray(orders)
            ? orders.find((item) => String(item.status || "").trim().toUpperCase() !== "RELEASED_FOR_USE") || null
            : null;
        renderOrderStatus(pendingOrder, plansByCode);
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Falha ao carregar área do cliente.";
      if (message.toLowerCase().includes("invalid user token")) {
        await session.logout();
        window.location.href = "./login.html?next=%2Fclient-area.html";
        return;
      }
      setStatus(message, "error");
      historyRows.innerHTML = '<tr><td colspan="5">Não foi possível carregar as conversões.</td></tr>';
      renderOrderRows([]);
      clearOrderStatus();
    } finally {
      forceUnlockTransientUi();
      scheduleForceUnlockTransientUi();
    }
  }

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async function () {
      support?.update({ enabled: false });
      closeAccountMenu();
      if (session) await session.logout();
      window.location.replace("./ofx-convert.html?logout=1");
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

  window.addEventListener("pageshow", () => {
    forceUnlockTransientUi();
    scheduleForceUnlockTransientUi();
  });

  window.addEventListener("focus", () => {
    forceUnlockTransientUi();
    scheduleForceUnlockTransientUi();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      forceUnlockTransientUi();
      scheduleForceUnlockTransientUi();
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    forceUnlockTransientUi();
    scheduleForceUnlockTransientUi();
  });

  forceUnlockTransientUi();
  scheduleForceUnlockTransientUi();
  bootstrapAccountPreview();
  void loadClientArea();
})();
