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
  const upgradeButtons = [document.getElementById("upgrade-top"), document.getElementById("upgrade-profile")].filter(Boolean);
  const upgradeOptions = document.getElementById("upgrade-options");
  const upgradePlans = document.getElementById("upgrade-plans");
  const orderRefresh = document.getElementById("order-refresh");
  const ordersRefreshStatus = document.getElementById("orders-refresh-status");
  const orderReviewLink = document.getElementById("order-review-link");
  const otherOpenOrders = document.getElementById("other-open-orders");
  const OPEN_ORDER_STATUSES = new Set(["REQUESTED", "PENDING", "AWAITING_PAYMENT"]);
  let account = null;
  let plansByCode = new Map();
  let openOrders = [];
  let ordersLoaded = false;
  let refreshInFlight = null;
  let refreshTimer = null;
  let pageStopped = false;
  let upgradeOpener = null;
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
    if (normalized === "SEND_PAYMENT_LINK") return "Seu pedido foi recebido. Aguarde o link de pagamento por e-mail.";
    if (normalized === "WAIT_FOR_PAYMENT") return "Conclua o pagamento para seguir com a liberação do seu plano.";
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
      if (url.username || url.password) return "";
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
    if (otherOpenOrders) otherOpenOrders.replaceChildren();
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
    if (!OPEN_ORDER_STATUSES.has(orderStatus)) {
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
    const link = orderStatus === "AWAITING_PAYMENT" ? safePaymentLink(data.payment_link) : "";
    if (orderNextStep) orderNextStep.textContent = orderStatus === "AWAITING_PAYMENT"
      ? (link ? formatOrderNextStep("WAIT_FOR_PAYMENT") : "Estamos preparando seu link de pagamento. Acompanhe o pedido ou fale com o suporte.")
      : formatOrderNextStep("SEND_PAYMENT_LINK");
    if (orderReviewLink) orderReviewLink.href = orderReviewUrl(data);
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
    const result = new Map();
    for (const item of items) {
      const code = String(item.code || "").trim().toLowerCase();
      if (code && (!result.has(code) || Number(item.version) > Number(result.get(code).version))) result.set(code, item);
    }
    return result;
  }

  function isOpenOrder(item) {
    return OPEN_ORDER_STATUSES.has(String(item.status || "").trim().toUpperCase());
  }

  function orderReviewUrl(item) {
    return `./checkout.html?plan=${encodeURIComponent(item.plan_code)}&intent=${encodeURIComponent(item.intent_id)}`;
  }

  function setUpgradeExpanded(expanded) {
    upgradeOptions?.classList.toggle("hidden", !expanded);
    upgradeButtons.forEach((button) => button.setAttribute("aria-expanded", String(expanded)));
  }

  function renderUpgrades() {
    const code = String(account?.plan_code || "").trim().toLowerCase();
    const current = plansByCode.get(code);
    const free = account && !code && account.quota_mode !== "pages";
    const candidates = !ordersLoaded || (!free && !current) ? [] : [...plansByCode.values()].filter((plan) =>
      plan.code !== code && Number.isFinite(Number(plan.price_cents)) &&
      Number(plan.price_cents) > (free ? 0 : Number(current.price_cents)) &&
      plan.currency === (current?.currency || "BRL") && plan.billing_period === (current?.billing_period || "monthly")
    ).sort((a, b) => Number(a.price_cents) - Number(b.price_cents));
    upgradeButtons.forEach((button) => button.classList.toggle("hidden", !candidates.length));
    if (!candidates.length) setUpgradeExpanded(false);
    if (!upgradePlans) return;
    // Keep focused links intact when a background refresh has no visible changes.
    const markup = candidates.map((plan) => {
      const existing = openOrders.find((item) => String(item.plan_code).toLowerCase() === String(plan.code).toLowerCase());
      const href = existing ? orderReviewUrl(existing) : `./checkout.html?plan=${encodeURIComponent(plan.code)}`;
      return `<article class="upgrade-plan">
        <h3>${escapeHtml(plan.name)}</h3>
        <p><strong>${escapeHtml(formatPriceBRL(plan.price_cents))}/mês</strong></p>
        <p>${Number(plan.quota_limit)} páginas por mês</p>
        <a class="client-action" href="${escapeHtml(href)}">${existing ? "Acompanhar pedido" : "Escolher plano"}</a>
      </article>`;
    }).join("");
    if (upgradePlans.innerHTML !== markup) upgradePlans.innerHTML = markup;
  }

  async function loadOrderData(requestedIntentId) {
    // The open list is authoritative. A network error must never mean completion.
    const payload = await fetchJson(`${apiBase}/checkout/intents/open`, { cache: "no-store" }, { attempts: 2 });
    if (!Array.isArray(payload.items)) throw new Error("Resposta de pedidos inválida.");
    const pending = payload.items.filter(isOpenOrder);
    const orders = [];
    const seenIntentIds = new Set();
    function pushOrder(item) {
      if (!item) return;
      const intentId = String(item.intent_id || "").trim();
      if (!intentId || seenIntentIds.has(intentId)) return;
      seenIntentIds.add(intentId);
      orders.push(item);
    }

    pending.forEach(pushOrder);
    if (requestedIntentId && !seenIntentIds.has(requestedIntentId)) {
      try {
        const requestedOrder = await fetchJson(
          `${apiBase}/checkout/intents/${encodeURIComponent(requestedIntentId)}`,
          { cache: "no-store" },
          { attempts: 2 },
        );
        pushOrder(requestedOrder);
      } catch (_error) {
        // Keep loading with fallback.
      }
    }

    try {
      const latestOrder = await fetchJson(`${apiBase}/checkout/intents/latest`, { cache: "no-store" }, { attempts: 2 });
      pushOrder(latestOrder);
    } catch (_error) {
      // Optional source for history.
    }
    return { orders, pending };
  }

  function renderAccount(me) {
    account = me;
    const quotaMode = String(me.quota_mode || "conversion").toLowerCase();
    if (profileEmail) profileEmail.textContent = me.email || "-";
    if (accountEmail) accountEmail.textContent = me.email || "-";
    if (accountAvatar) accountAvatar.textContent = getInitialLabel(me.name || me.email || "U");
    if (quotaText) quotaText.textContent = `${me.quota_remaining} / ${me.quota_limit}`;
    if (planText) planText.textContent = String(me.plan_name || "").trim() || "Plano gratuito";
    if (planSummary) planSummary.textContent = String(me.plan_name || "").trim() || (quotaMode === "pages" ? "Plano pago" : "Plano gratuito");
    if (planModeSummary) planModeSummary.textContent = quotaMode === "pages" ? "Pago por páginas" : "Gratuito por conversões";
    if (activationHintText) activationHintText.textContent = quotaMode === "pages"
      ? "Seu plano está ativo. Você já pode usar todas as funcionalidades da conta."
      : "Você ainda está no plano gratuito. Ative um plano para ampliar limites e liberar novos recursos.";
    support?.update({
      enabled: quotaMode === "pages" && Boolean(me.plan_code),
      message: me.plan_name
        ? `Olá! Sou cliente do OFX Simples, tenho o plano ${me.plan_name} e preciso de suporte.`
        : "Olá! Sou cliente do OFX Simples e preciso de suporte.",
    });
  }

  function scheduleOrderRefresh() {
    window.clearTimeout(refreshTimer);
    refreshTimer = null;
    if (pageStopped || document.visibilityState !== "visible" || (ordersLoaded && !openOrders.length)) return;
    refreshTimer = window.setTimeout(() => { void refreshAccountOrders(); }, 30000);
  }

  function refreshAccountOrders() {
    if (pageStopped || !session) return Promise.resolve();
    if (refreshInFlight) return refreshInFlight;
    window.clearTimeout(refreshTimer);
    if (orderRefresh) orderRefresh.disabled = true;
    refreshInFlight = (async () => {
      try {
        const requestedIntentId = new URL(window.location.href).searchParams.get("checkout_intent") || "";
        const [me, data] = await Promise.all([
          fetchJson(`${apiBase}/auth/me`, { cache: "no-store" }, { attempts: 2 }),
          loadOrderData(requestedIntentId),
        ]);
        if (pageStopped) return;
        renderAccount(me);
        openOrders = data.pending;
        ordersLoaded = true;
        renderOrderRows(data.orders);
        renderOrderStatus(openOrders[0] || null, plansByCode);
        if (otherOpenOrders) otherOpenOrders.innerHTML = openOrders.slice(1).map((item) =>
          `<p><strong>${escapeHtml(item.plan_name)}</strong> · ${escapeHtml(formatOrderStatus(item.status))}
          <a href="${escapeHtml(orderReviewUrl(item))}">Acompanhar pedido</a></p>`
        ).join("");
        if (ordersRefreshStatus) ordersRefreshStatus.textContent = "";
        renderUpgrades();
      } catch (_error) {
        if (pageStopped) return;
        ordersLoaded = false;
        if (ordersRefreshStatus) ordersRefreshStatus.textContent = "Não foi possível atualizar seus pedidos. Tentaremos novamente em instantes.";
        if (!openOrders.length && ordersRows) ordersRows.innerHTML = '<tr><td colspan="5">Não foi possível consultar seus pedidos.</td></tr>';
        renderUpgrades();
      } finally {
        refreshInFlight = null;
        if (orderRefresh) orderRefresh.disabled = false;
        scheduleOrderRefresh();
      }
    })();
    return refreshInFlight;
  }

  async function loadClientArea() {
    support?.update({ enabled: false });
    try {
      const me = await fetchJson(`${apiBase}/auth/me`, undefined, { attempts: 3 });
      renderAccount(me);
      setStatus("", null);
      void fetchJson(`${apiBase}/telemetry/events`, {
        method: "POST", headers: { "content-type": "application/json" }, keepalive: true,
        body: JSON.stringify({ event_type: "client_area_view", page_path: "/client-area.html" }),
      }).catch(() => {});
      // History and catalog failures must not prevent tracking a pending order.
      void fetchJson(`${apiBase}/client/conversions?limit=20`, undefined, { attempts: 3 }).then((history) => {
        renderRows(history.items || []);
        if (viewAllLink) viewAllLink.style.visibility = (history.items || []).length < 20 ? "hidden" : "visible";
      }).catch(() => {
        historyRows.innerHTML = '<tr><td colspan="5">Não foi possível carregar as conversões.</td></tr>';
      });
      void fetchJson(`${apiBase}/plans`, undefined, { attempts: 3 }).then((catalog) => {
        plansByCode = mapPlansByCode(catalog);
        renderUpgrades();
        if (ordersLoaded) renderOrderStatus(openOrders[0] || null, plansByCode);
      }).catch(() => {});
      await refreshAccountOrders();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Falha ao carregar área do cliente.";
      if (message.toLowerCase().includes("invalid user token")) {
        await session.logout();
        window.location.href = "./login.html?next=%2Fclient-area.html";
        return;
      }
      setStatus(message, "error");
    } finally {
      forceUnlockTransientUi();
      scheduleForceUnlockTransientUi();
    }
  }

  upgradeButtons.forEach((button) => button.addEventListener("click", () => {
    upgradeOpener = button;
    setUpgradeExpanded(true);
    document.getElementById("upgrade-title")?.focus();
    upgradeOptions?.scrollIntoView({ block: "start" });
  }));
  document.getElementById("upgrade-close")?.addEventListener("click", () => {
    setUpgradeExpanded(false);
    upgradeOpener?.focus();
  });
  orderRefresh?.addEventListener("click", () => { void refreshAccountOrders(); });

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async function () {
      pageStopped = true;
      window.clearTimeout(refreshTimer);
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

  window.addEventListener("pagehide", () => {
    pageStopped = true;
    window.clearTimeout(refreshTimer);
  });

  window.addEventListener("pageshow", (event) => {
    pageStopped = false;
    if (account && event.persisted) void refreshAccountOrders();
    forceUnlockTransientUi();
    scheduleForceUnlockTransientUi();
  });

  window.addEventListener("focus", () => {
    if (account) void refreshAccountOrders();
    forceUnlockTransientUi();
    scheduleForceUnlockTransientUi();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      if (account) void refreshAccountOrders();
      forceUnlockTransientUi();
      scheduleForceUnlockTransientUi();
    } else {
      window.clearTimeout(refreshTimer);
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
