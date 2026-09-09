(function () {
  const ORDER_PAGE_SIZE = 10;
  const USER_PAGE_SIZE = 20;

  const loginCard = document.getElementById("admin-login-card");
  const navigationNode = document.getElementById("admin-navigation");
  const loginForm = document.getElementById("admin-login-form");
  const loginBtn = document.getElementById("admin-login-btn");
  const loginStatusNode = document.getElementById("admin-login-status");
  const logoutBtn = document.getElementById("admin-logout-btn");

  const dashboardPeriodNode = document.getElementById("dashboard-period");
  const dashboardIdentityTypeNode = document.getElementById("dashboard-identity-type");
  const dashboardRefreshBtn = document.getElementById("dashboard-refresh-btn");
  const dashboardStatusNode = document.getElementById("dashboard-status");
  const dashboardSummaryNode = document.getElementById("dashboard-summary");
  const dashboardDailyChartNode = document.getElementById("dashboard-daily-chart");
  const dashboardIdentitiesNode = document.getElementById("dashboard-identities");
  const dashboardTopErrorsNode = document.getElementById("dashboard-top-errors");
  const dashboardRecentAttentionNode = document.getElementById("dashboard-recent-attention");
  const adminSectionButtons = document.querySelectorAll("[data-admin-section]");
  const adminPanelNodes = document.querySelectorAll("[data-admin-panel]");

  const refreshBtn = document.getElementById("orders-refresh-btn");
  const filterNode = document.getElementById("orders-filter");
  const queryNode = document.getElementById("orders-query");
  const prevBtn = document.getElementById("orders-prev-btn");
  const nextBtn = document.getElementById("orders-next-btn");
  const pageLabelNode = document.getElementById("orders-page-label");
  const statusNode = document.getElementById("admin-status");
  const ordersListNode = document.getElementById("orders-list");
  const emptyNode = document.getElementById("orders-empty");

  const usersRefreshBtn = document.getElementById("users-refresh-btn");
  const usersQueryNode = document.getElementById("users-query");
  const usersFilterNode = document.getElementById("users-filter");
  const usersStatusNode = document.getElementById("users-status");
  const usersListNode = document.getElementById("users-list");

  let ordersOffset = 0;
  let ordersTotal = 0;
  let usersOffset = 0;
  let activeAdminSection = "dashboard";
  let isAdminAuthenticated = false;

  function resolveApiBase() {
    const host = window.location.hostname;
    const port = window.location.port;
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    const isDevFrontend = isLocalHost && port !== "8000";
    if (isDevFrontend) return `http://${host}:8000`;
    if (window.location.origin && window.location.origin !== "null") return window.location.origin;
    return "http://127.0.0.1:8000";
  }

  function setStatus(message, kind) {
    const text = String(message || "");
    const nodes = [statusNode, loginStatusNode];
    nodes.forEach(function (node) {
      if (!node) return;
      node.textContent = text;
      node.className = "status";
      if (kind) node.classList.add(kind);
    });
  }

  function setUsersStatus(message, kind) {
    if (!usersStatusNode) return;
    usersStatusNode.textContent = String(message || "");
    usersStatusNode.className = "status";
    if (kind) usersStatusNode.classList.add(kind);
  }

  function setDashboardStatus(message, kind) {
    if (!dashboardStatusNode) return;
    dashboardStatusNode.textContent = String(message || "");
    dashboardStatusNode.className = "status";
    if (kind) dashboardStatusNode.classList.add(kind);
  }

  function setActiveAdminSection(section) {
    const normalizedSection = ["dashboard", "orders", "users"].includes(section) ? section : "dashboard";
    activeAdminSection = normalizedSection;
    adminSectionButtons.forEach(function (button) {
      const isActive = String(button.dataset.adminSection || "") === normalizedSection;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    adminPanelNodes.forEach(function (panel) {
      const isActive = String(panel.dataset.adminPanel || "") === normalizedSection;
      panel.classList.toggle("hidden", !isAdminAuthenticated || !isActive);
    });
  }

  function setAuthenticatedView(isAuthenticated) {
    isAdminAuthenticated = isAuthenticated;
    if (loginCard) loginCard.classList.toggle("hidden", isAuthenticated);
    if (navigationNode) navigationNode.classList.toggle("hidden", !isAuthenticated);
    setActiveAdminSection(activeAdminSection);
  }

  function mapStatusLabel(status) {
    const normalized = String(status || "").trim().toUpperCase();
    if (normalized === "REQUESTED" || normalized === "PENDING") return "Solicitado";
    if (normalized === "AWAITING_PAYMENT") return "Aguardando pagamento";
    if (normalized === "RELEASED_FOR_USE") return "Liberado para uso";
    return normalized || "-";
  }

  function mapNextStepLabel(nextStep) {
    const normalized = String(nextStep || "").trim().toUpperCase();
    if (normalized === "SEND_PAYMENT_LINK") return "Enviar link de pagamento";
    if (normalized === "WAIT_FOR_PAYMENT") return "Aguardar pagamento";
    if (normalized === "READY_TO_USE") return "Plano liberado";
    return "Revisar pedido";
  }

  function formatDateTime(value) {
    const raw = String(value || "").trim();
    if (!raw) return "-";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return raw;
    return parsed.toLocaleString("pt-BR");
  }

  function formatPriceBRL(priceCents) {
    const amount = Number(priceCents || 0) / 100;
    return amount.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function formatInteger(value) {
    return Math.max(0, Number(value || 0)).toLocaleString("pt-BR", { maximumFractionDigits: 0 });
  }

  function formatPercent(value) {
    return `${Number(value || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
  }

  function formatCountLabel(value, singular, plural) {
    const count = Math.max(0, Number(value || 0));
    return `${formatInteger(count)} ${count === 1 ? singular : plural}`;
  }

  function formatDuration(durationMs) {
    const value = Math.max(0, Number(durationMs || 0));
    if (!value) return "-";
    if (value < 1000) return `${formatInteger(value)} ms`;
    return `${(value / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s`;
  }

  function formatShortDate(value) {
    const parsed = new Date(`${String(value || "")}T12:00:00`);
    if (Number.isNaN(parsed.getTime())) return String(value || "-");
    return parsed.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  }

  function badgeClass(status) {
    const normalized = String(status || "").trim().toUpperCase();
    if (normalized === "AWAITING_PAYMENT") return "badge awaiting";
    if (normalized === "RELEASED_FOR_USE") return "badge released";
    return "badge";
  }

  function createTextElement(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    node.textContent = String(text ?? "");
    return node;
  }

  function appendLabeledParagraph(parent, label, value) {
    const paragraph = document.createElement("p");
    paragraph.appendChild(createTextElement("strong", "", `${label}:`));
    paragraph.appendChild(document.createTextNode(` ${String(value ?? "-")}`));
    parent.appendChild(paragraph);
  }

  function createActionButton(action, idKey, idValue, label, options) {
    const settings = options || {};
    const button = createTextElement("button", settings.className || "", label);
    button.dataset.action = action;
    button.dataset[idKey] = String(idValue || "");
    button.disabled = !!settings.disabled;
    return button;
  }

  function setHistoryMessage(historyNode, message) {
    historyNode.replaceChildren(createTextElement("p", "", message));
  }

  function appendHistoryLine(historyNode, emphasizedText, detailText) {
    const paragraph = document.createElement("p");
    paragraph.appendChild(createTextElement("strong", "", emphasizedText));
    paragraph.appendChild(document.createTextNode(` ${String(detailText || "")}`));
    historyNode.appendChild(paragraph);
  }

  function appendMetricCard(parent, label, value, detail, tone) {
    const card = document.createElement("article");
    card.className = `metric-card${tone ? ` ${tone}` : ""}`;
    card.appendChild(createTextElement("p", "metric-label", label));
    card.appendChild(createTextElement("strong", "metric-value", value));
    card.appendChild(createTextElement("p", "metric-detail", detail));
    parent.appendChild(card);
  }

  function renderDashboardSummary(summary) {
    if (!dashboardSummaryNode) return;
    dashboardSummaryNode.replaceChildren();
    const total = Number(summary.conversions_total || 0);
    const successCount = Number(summary.technical_success_count || 0);
    const cleanCount = Number(summary.clean_conversion_count || 0);
    const reviewCount = Math.max(0, successCount - cleanCount);
    appendMetricCard(dashboardSummaryNode, "Conversões", formatInteger(total), "tentativas no período", "");
    appendMetricCard(
      dashboardSummaryNode,
      "Sucesso técnico",
      formatPercent(summary.technical_success_rate),
      formatCountLabel(successCount, "concluída", "concluídas"),
      "clean",
    );
    appendMetricCard(
      dashboardSummaryNode,
      "Sem alertas técnicos",
      formatPercent(summary.clean_conversion_rate),
      formatCountLabel(cleanCount, "conversão", "conversões"),
      "clean",
    );
    appendMetricCard(
      dashboardSummaryNode,
      "Revisar",
      formatInteger(reviewCount),
      "concluídas com sinal de atenção",
      "review",
    );
    appendMetricCard(
      dashboardSummaryNode,
      "Falhas",
      formatInteger(summary.failure_count),
      "tentativas não concluídas",
      Number(summary.failure_count || 0) > 0 ? "failure" : "",
    );
    appendMetricCard(
      dashboardSummaryNode,
      "Pessoas ativas",
      formatInteger(summary.active_people_count),
      "pessoas que converteram",
      "",
    );
    appendMetricCard(
      dashboardSummaryNode,
      "Voltaram a converter",
      formatInteger(summary.returning_people_count),
      "atividade em um dia posterior",
      "",
    );
    appendMetricCard(
      dashboardSummaryNode,
      "Tempo mediano",
      formatDuration(summary.median_duration_ms),
      "metade concluiu até esse tempo",
      "",
    );
  }

  function renderDashboardChart(items) {
    if (!dashboardDailyChartNode) return;
    dashboardDailyChartNode.replaceChildren();
    const dailyItems = Array.isArray(items) ? items : [];
    const total = dailyItems.reduce(function (sum, item) {
      return sum + Number(item.conversions || 0);
    }, 0);
    if (!dailyItems.length || total === 0) {
      dashboardDailyChartNode.appendChild(
        createTextElement("p", "empty dashboard-empty", "Ainda não há conversões neste período."),
      );
      return;
    }

    const maxDaily = Math.max(
      1,
      ...dailyItems.map(function (item) {
        return Number(item.conversions || 0);
      }),
    );
    const bars = document.createElement("div");
    bars.className = "chart-bars";
    dailyItems.forEach(function (item) {
      const conversions = Number(item.conversions || 0);
      const clean = Number(item.clean || 0);
      const review = Number(item.review || 0);
      const failures = Number(item.failures || 0);
      const day = document.createElement("div");
      day.className = "chart-day";
      day.setAttribute(
        "aria-label",
        `${formatShortDate(item.date)}: ${formatInteger(conversions)} conversões, ${formatInteger(clean)} sem alertas, ${formatInteger(review)} para revisar e ${formatInteger(failures)} falhas.`,
      );
      day.appendChild(createTextElement("span", "chart-total", formatInteger(conversions)));

      const stack = document.createElement("div");
      stack.className = "chart-stack";
      [
        ["clean", clean],
        ["review", review],
        ["failure", failures],
      ].forEach(function (entry) {
        const segment = document.createElement("span");
        segment.className = `chart-segment ${entry[0]}`;
        segment.style.height = `${(Number(entry[1]) / maxDaily) * 140}px`;
        stack.appendChild(segment);
      });
      day.appendChild(stack);
      day.appendChild(createTextElement("span", "chart-day-label", formatShortDate(item.date)));
      bars.appendChild(day);
    });
    dashboardDailyChartNode.appendChild(bars);
    dashboardDailyChartNode.scrollLeft = dashboardDailyChartNode.scrollWidth;
  }

  function appendIdentityRow(parent, label, conversions, people) {
    const row = document.createElement("div");
    row.className = "identity-row";
    const text = document.createElement("div");
    text.appendChild(createTextElement("p", "", label));
    text.appendChild(createTextElement("p", "muted compact", formatCountLabel(people, "pessoa", "pessoas")));
    row.appendChild(text);
    row.appendChild(createTextElement("strong", "", formatCountLabel(conversions, "conversão", "conversões")));
    parent.appendChild(row);
  }

  function renderDashboardIdentities(identities) {
    if (!dashboardIdentitiesNode) return;
    dashboardIdentitiesNode.replaceChildren();
    appendIdentityRow(
      dashboardIdentitiesNode,
      "Pessoas cadastradas",
      identities.registered_conversions,
      identities.registered_people,
    );
    appendIdentityRow(
      dashboardIdentitiesNode,
      "Pessoas anônimas",
      identities.anonymous_conversions,
      identities.anonymous_people,
    );
  }

  function renderDashboardErrors(items) {
    if (!dashboardTopErrorsNode) return;
    dashboardTopErrorsNode.replaceChildren();
    const errors = Array.isArray(items) ? items : [];
    if (!errors.length) {
      dashboardTopErrorsNode.appendChild(
        createTextElement("p", "empty dashboard-empty", "Nenhuma falha registrada neste período."),
      );
      return;
    }
    errors.forEach(function (item) {
      const row = document.createElement("div");
      row.className = "compact-list-item";
      const text = document.createElement("div");
      const errorCode = String(item.error_code || "unknown") === "unknown" ? "Código não informado" : item.error_code;
      const errorStage = String(item.error_stage || "unknown") === "unknown" ? "etapa não informada" : item.error_stage;
      text.appendChild(createTextElement("p", "", errorCode));
      text.appendChild(createTextElement("p", "muted compact", errorStage));
      row.appendChild(text);
      row.appendChild(createTextElement("strong", "", formatInteger(item.count)));
      dashboardTopErrorsNode.appendChild(row);
    });
  }

  function appendTableCell(row, text, tagName) {
    const cell = createTextElement(tagName || "td", "", text);
    row.appendChild(cell);
    return cell;
  }

  function renderDashboardAttention(items) {
    if (!dashboardRecentAttentionNode) return;
    dashboardRecentAttentionNode.replaceChildren();
    const attentionItems = Array.isArray(items) ? items : [];
    if (!attentionItems.length) {
      dashboardRecentAttentionNode.appendChild(
        createTextElement("p", "empty dashboard-empty", "Nenhuma conversão precisa de atenção neste período."),
      );
      return;
    }

    const table = document.createElement("table");
    table.className = "dashboard-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["Data", "Pessoa", "Banco/modelo", "Resultado", "Motivo", "Identificador"].forEach(function (label) {
      appendTableCell(headRow, label, "th");
    });
    head.appendChild(headRow);
    table.appendChild(head);

    const body = document.createElement("tbody");
    attentionItems.forEach(function (item) {
      const row = document.createElement("tr");
      appendTableCell(row, formatDateTime(item.created_at));
      appendTableCell(row, String(item.identity_type || "") === "registered" ? "Cadastrada" : "Anônima");
      appendTableCell(row, item.model || "Não identificado");
      appendTableCell(row, item.status || "Não informado");
      appendTableCell(row, item.issue_reason || "Revisão recomendada");
      appendTableCell(row, item.processing_id || "-");
      body.appendChild(row);
    });
    table.appendChild(body);
    dashboardRecentAttentionNode.appendChild(table);
  }

  function renderDashboard(payload) {
    renderDashboardSummary(payload.summary || {});
    renderDashboardChart(payload.daily || []);
    renderDashboardIdentities(payload.identities || {});
    renderDashboardErrors(payload.top_errors || []);
    renderDashboardAttention(payload.recent_attention || []);
  }

  async function tryRefreshAdminSession() {
    const response = await fetch(`${resolveApiBase()}/auth/session/refresh`, {
      method: "POST",
      credentials: "include",
    });
    return response.ok;
  }

  async function apiRequest(path, init, allowRetry) {
    const response = await fetch(`${resolveApiBase()}${path}`, Object.assign({}, init || {}, { credentials: "include" }));
    if (response.status === 401 && allowRetry !== false) {
      const refreshed = await tryRefreshAdminSession();
      if (refreshed) {
        return apiRequest(path, init, false);
      }
    }
    const payload = await response.json().catch(function () {
      return {};
    });
    return { response, payload };
  }

  async function loadDashboard() {
    if (!dashboardPeriodNode || !dashboardIdentityTypeNode) return;
    const days = String(dashboardPeriodNode.value || "30");
    const identityType = String(dashboardIdentityTypeNode.value || "all");
    setDashboardStatus("Carregando indicadores...", null);
    if (dashboardRefreshBtn) dashboardRefreshBtn.disabled = true;
    try {
      const { response, payload } = await apiRequest(
        `/admin/dashboard?days=${encodeURIComponent(days)}&identity_type=${encodeURIComponent(identityType)}`,
      );
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          setAuthenticatedView(false);
        }
        setDashboardStatus(String(payload.detail || "Não foi possível carregar os indicadores."), "error");
        return;
      }
      renderDashboard(payload);
      setDashboardStatus(`Indicadores atualizados em ${formatDateTime(payload.end_at)}.`, "ok");
    } catch (_error) {
      setDashboardStatus("Falha de rede ao carregar os indicadores.", "error");
    } finally {
      if (dashboardRefreshBtn) dashboardRefreshBtn.disabled = false;
    }
  }

  async function verifyAdminSession() {
    try {
      const { response } = await apiRequest("/admin/me");
      if (!response.ok) {
        setAuthenticatedView(false);
        return;
      }
      setAuthenticatedView(true);
      await loadDashboard();
    } catch (_error) {
      setStatus("Falha de rede ao validar sessão admin.", "error");
      setAuthenticatedView(false);
    }
  }

  function updatePager() {
    if (pageLabelNode) {
      const currentPage = Math.floor(ordersOffset / ORDER_PAGE_SIZE) + 1;
      const totalPages = Math.max(1, Math.ceil(ordersTotal / ORDER_PAGE_SIZE));
      pageLabelNode.textContent = `Página ${currentPage} de ${totalPages}`;
    }
    if (prevBtn) prevBtn.disabled = ordersOffset <= 0;
    if (nextBtn) nextBtn.disabled = ordersOffset + ORDER_PAGE_SIZE >= ordersTotal;
  }

  async function loadOrders() {
    if (!ordersListNode || !emptyNode || !filterNode) return;
    const filter = String(filterNode.value || "open");
    const query = String(queryNode?.value || "").trim();
    setStatus("Carregando pedidos...", null);
    try {
      const { response, payload } = await apiRequest(
        `/admin/checkout/intents?status=${encodeURIComponent(filter)}&query=${encodeURIComponent(query)}&limit=${ORDER_PAGE_SIZE}&offset=${ordersOffset}`,
      );
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          setAuthenticatedView(false);
        }
        setStatus(String(payload.detail || "Não foi possível carregar os pedidos."), "error");
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      ordersTotal = Number(payload.total || 0);
      renderOrders(items);
      updatePager();
      setStatus(`Pedidos carregados: ${items.length} de ${ordersTotal}.`, "ok");
    } catch (_error) {
      setStatus("Falha de rede ao carregar pedidos.", "error");
    }
  }

  async function loadUsers() {
    if (!usersListNode) return;
    const query = String(usersQueryNode?.value || "").trim();
    const rawFilter = String(usersFilterNode?.value || "all");
    const filterParam =
      rawFilter === "admin"
        ? "&only_admin=true"
        : rawFilter === "non_admin"
          ? "&only_admin=false"
          : rawFilter === "active"
            ? "&only_active=true"
            : rawFilter === "inactive"
              ? "&only_active=false"
              : "";
    setUsersStatus("Carregando usuários...", null);
    try {
      const { response, payload } = await apiRequest(
        `/admin/users?query=${encodeURIComponent(query)}${filterParam}&limit=${USER_PAGE_SIZE}&offset=${usersOffset}`,
      );
      if (!response.ok) {
        setUsersStatus(String(payload.detail || "Não foi possível carregar usuários."), "error");
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      renderUsers(items);
      setUsersStatus(`Usuários carregados: ${items.length} de ${Number(payload.total || 0)}.`, "ok");
    } catch (_error) {
      setUsersStatus("Falha de rede ao carregar usuários.", "error");
    }
  }

  function buildOrderCard(order) {
    const container = document.createElement("article");
    container.className = "order-card";
    const status = String(order.status || "");
    const isReleased = status.toUpperCase() === "RELEASED_FOR_USE";
    const hasUserReference =
      String(order.user_id || "").trim().length > 0 || String(order.customer_email || "").trim().length > 0;
    const canRelease = !isReleased && hasUserReference;

    const head = document.createElement("div");
    head.className = "order-head";
    const titleBlock = document.createElement("div");
    titleBlock.appendChild(createTextElement("h3", "order-title", `Protocolo ${String(order.intent_id || "-")}`));
    titleBlock.appendChild(createTextElement("p", "order-meta", `Criado em ${formatDateTime(order.created_at)}`));
    head.appendChild(titleBlock);
    head.appendChild(createTextElement("span", badgeClass(status), mapStatusLabel(status)));
    container.appendChild(head);

    const grid = document.createElement("div");
    grid.className = "grid";
    appendLabeledParagraph(grid, "Plano", order.plan_name || "-");
    appendLabeledParagraph(grid, "Valor", formatPriceBRL(order.price_cents));
    appendLabeledParagraph(grid, "Cliente", order.customer_name || "-");
    appendLabeledParagraph(grid, "E-mail", order.customer_email || "-");
    appendLabeledParagraph(grid, "WhatsApp", order.customer_whatsapp || "-");
    appendLabeledParagraph(grid, "User ID", order.user_id || "-");
    appendLabeledParagraph(grid, "Próximo passo", mapNextStepLabel(order.next_step));
    appendLabeledParagraph(grid, "Link atual", order.payment_link || "-");
    container.appendChild(grid);

    const actions = document.createElement("div");
    actions.className = "admin-actions";
    const inline = document.createElement("div");
    inline.className = "inline";
    const paymentInput = document.createElement("input");
    paymentInput.dataset.action = "payment-link-input";
    paymentInput.type = "url";
    paymentInput.placeholder = "https://pagamento.exemplo/link";
    paymentInput.value = String(order.payment_link || "");
    inline.appendChild(paymentInput);
    inline.appendChild(
      createActionButton("send-link", "intentId", order.intent_id, "Enviar link", { disabled: isReleased }),
    );
    actions.appendChild(inline);

    const buttonRow = document.createElement("div");
    buttonRow.className = "pill-row";
    buttonRow.appendChild(
      createActionButton("release", "intentId", order.intent_id, "Liberar plano", {
        className: "ghost",
        disabled: !canRelease,
      }),
    );
    buttonRow.appendChild(
      createActionButton("history", "intentId", order.intent_id, "Ver histórico", { className: "ghost" }),
    );
    actions.appendChild(buttonRow);
    const history = document.createElement("div");
    history.className = "history hidden";
    history.dataset.role = "history";
    actions.appendChild(history);
    container.appendChild(actions);
    return container;
  }

  function renderOrders(items) {
    if (!ordersListNode || !emptyNode) return;
    ordersListNode.replaceChildren();
    if (!items.length) {
      emptyNode.classList.remove("hidden");
      return;
    }
    emptyNode.classList.add("hidden");
    items.forEach(function (order) {
      ordersListNode.appendChild(buildOrderCard(order));
    });
  }

  function renderUsers(items) {
    if (!usersListNode) return;
    usersListNode.replaceChildren();
    items.forEach(function (user) {
      const isAdmin = !!user.is_admin;
      const isActive = user.is_active !== false;
      const isEmailVerified = String(user.email_verification_status || "verified") === "verified";
      const card = document.createElement("article");
      card.className = "order-card";
      const head = document.createElement("div");
      head.className = "order-head";
      const identity = document.createElement("div");
      identity.appendChild(createTextElement("h3", "order-title", user.name || "-"));
      identity.appendChild(createTextElement("p", "order-meta", user.email || "-"));
      head.appendChild(identity);
      const badges = document.createElement("div");
      badges.className = "pill-row";
      badges.appendChild(createTextElement("span", `badge ${isActive ? "released" : "awaiting"}`, isActive ? "Ativo" : "Inativo"));
      badges.appendChild(createTextElement("span", `badge ${isAdmin ? "released" : ""}`, isAdmin ? "Admin" : "Usuário"));
      badges.appendChild(
        createTextElement(
          "span",
          `badge ${isEmailVerified ? "released" : "awaiting"}`,
          isEmailVerified ? "E-mail confirmado" : "E-mail pendente",
        ),
      );
      head.appendChild(badges);
      card.appendChild(head);

      const grid = document.createElement("div");
      grid.className = "grid";
      appendLabeledParagraph(grid, "User ID", user.user_id || "-");
      appendLabeledParagraph(grid, "Criado", formatDateTime(user.created_at));
      appendLabeledParagraph(grid, "Atualizado", formatDateTime(user.updated_at));
      appendLabeledParagraph(grid, "Retornos registrados", Number(user.login_count || 0));
      appendLabeledParagraph(grid, "Login com senha", Number(user.local_login_count || 0));
      appendLabeledParagraph(grid, "Login com Google", Number(user.google_login_count || 0));
      appendLabeledParagraph(grid, "Último login", formatDateTime(user.last_login_at));
      card.appendChild(grid);

      const buttonRow = document.createElement("div");
      buttonRow.className = "pill-row";
      const roleButton = createActionButton(
        "toggle-role",
        "userId",
        user.user_id,
        isAdmin ? "Revogar admin" : "Promover a admin",
        { className: "ghost" },
      );
      roleButton.dataset.isAdmin = isAdmin ? "1" : "0";
      buttonRow.appendChild(roleButton);
      const statusButton = createActionButton(
        "toggle-status",
        "userId",
        user.user_id,
        isActive ? "Inativar usuário" : "Reativar usuário",
        { className: "ghost" },
      );
      statusButton.dataset.isActive = isActive ? "1" : "0";
      buttonRow.appendChild(statusButton);
      buttonRow.appendChild(
        createActionButton("user-login-history", "userId", user.user_id, "Ver histórico de logins", {
          className: "ghost",
        }),
      );
      buttonRow.appendChild(
        createActionButton("user-role-history", "userId", user.user_id, "Ver histórico de permissões", {
          className: "ghost",
        }),
      );
      card.appendChild(buttonRow);
      const loginHistory = document.createElement("div");
      loginHistory.className = "history hidden";
      loginHistory.dataset.role = "user-login-history";
      card.appendChild(loginHistory);
      const roleHistory = document.createElement("div");
      roleHistory.className = "history hidden";
      roleHistory.dataset.role = "user-role-history";
      card.appendChild(roleHistory);
      usersListNode.appendChild(card);
    });
  }

  async function sendPaymentLink(intentId, paymentLink) {
    const link = String(paymentLink || "").trim();
    if (!link) {
      setStatus("Informe o link de pagamento.", "error");
      return;
    }
    setStatus("Enviando link de pagamento...", null);
    try {
      const { response, payload } = await apiRequest(`/admin/checkout/intents/${encodeURIComponent(intentId)}/payment-link`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ payment_link: link }),
      });
      if (!response.ok) {
        setStatus(String(payload.detail || "Não foi possível salvar o link de pagamento."), "error");
        return;
      }
      setStatus("Link enviado e pedido atualizado.", "ok");
      await loadOrders();
    } catch (_error) {
      setStatus("Falha de rede ao enviar link.", "error");
    }
  }

  async function releaseOrder(intentId) {
    setStatus("Liberando plano...", null);
    try {
      const { response, payload } = await apiRequest(`/admin/checkout/intents/${encodeURIComponent(intentId)}/release`, {
        method: "POST",
      });
      if (!response.ok) {
        setStatus(String(payload.detail || "Não foi possível liberar o plano."), "error");
        return;
      }
      setStatus("Plano liberado com sucesso.", "ok");
      await loadOrders();
    } catch (_error) {
      setStatus("Falha de rede ao liberar plano.", "error");
    }
  }

  async function loadOrderHistory(intentId, historyNode) {
    if (!historyNode) return;
    historyNode.classList.remove("hidden");
    setHistoryMessage(historyNode, "Carregando histórico...");
    try {
      const { response, payload } = await apiRequest(
        `/admin/checkout/intents/${encodeURIComponent(intentId)}/history?limit=20`,
      );
      if (!response.ok) {
        setHistoryMessage(historyNode, String(payload.detail || "Falha ao carregar histórico."));
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        setHistoryMessage(historyNode, "Nenhum evento registrado.");
        return;
      }
      historyNode.replaceChildren();
      items.forEach(function (item) {
        const when = formatDateTime(item.created_at);
        const who = String(item.actor_kind || "system");
        const message = String(item.event_message || item.event_type || "-");
        appendHistoryLine(historyNode, when, `[${who}] ${message}`);
      });
    } catch (_error) {
      setHistoryMessage(historyNode, "Falha de rede ao carregar histórico.");
    }
  }

  async function toggleUserRole(userId, currentIsAdmin) {
    const targetState = !currentIsAdmin;
    setUsersStatus(targetState ? "Promovendo usuário..." : "Revogando acesso admin...", null);
    try {
      const { response, payload } = await apiRequest("/admin/users/role", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ user_id: userId, is_admin: targetState }),
      });
      if (!response.ok) {
        setUsersStatus(String(payload.detail || "Não foi possível atualizar o acesso."), "error");
        return;
      }
      setUsersStatus("Acesso atualizado.", "ok");
      await loadUsers();
    } catch (_error) {
      setUsersStatus("Falha de rede ao atualizar acesso.", "error");
    }
  }

  async function toggleUserStatus(userId, currentIsActive) {
    const targetState = !currentIsActive;
    if (
      !targetState &&
      !window.confirm("Inativar este usuário? As sessões atuais serão encerradas e novos acessos serão bloqueados.")
    ) {
      return;
    }
    setUsersStatus(targetState ? "Reativando usuário..." : "Inativando usuário...", null);
    try {
      const { response, payload } = await apiRequest("/admin/users/status", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ user_id: userId, is_active: targetState }),
      });
      if (!response.ok) {
        setUsersStatus(String(payload.detail || "Não foi possível atualizar o status do usuário."), "error");
        return;
      }
      setUsersStatus(targetState ? "Usuário reativado." : "Usuário inativado.", "ok");
      await loadUsers();
    } catch (_error) {
      setUsersStatus("Falha de rede ao atualizar o status do usuário.", "error");
    }
  }

  async function loadUserRoleHistory(userId, historyNode) {
    if (!historyNode) return;
    historyNode.classList.remove("hidden");
    setHistoryMessage(historyNode, "Carregando histórico de permissões...");
    try {
      const { response, payload } = await apiRequest(
        `/admin/users/${encodeURIComponent(userId)}/history?limit=20`,
      );
      if (!response.ok) {
        setHistoryMessage(historyNode, String(payload.detail || "Falha ao carregar histórico de permissões."));
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        setHistoryMessage(historyNode, "Nenhuma alteração de permissão registrada.");
        return;
      }
      historyNode.replaceChildren();
      items.forEach(function (item) {
        const when = formatDateTime(item.created_at);
        const actor = String(item.actor_email || item.actor_user_id || "sistema");
        const label = String(item.new_is_admin ? "Promovido para admin" : "Revogado admin");
        appendHistoryLine(historyNode, when, `${label} por ${actor}`);
      });
    } catch (_error) {
      setHistoryMessage(historyNode, "Falha de rede ao carregar histórico de permissões.");
    }
  }

  async function loadUserLoginHistory(userId, historyNode) {
    if (!historyNode) return;
    historyNode.classList.remove("hidden");
    setHistoryMessage(historyNode, "Carregando histórico de logins...");
    try {
      const { response, payload } = await apiRequest(
        `/admin/users/${encodeURIComponent(userId)}/login-history?limit=20`,
      );
      if (!response.ok) {
        setHistoryMessage(historyNode, String(payload.detail || "Falha ao carregar histórico de logins."));
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        setHistoryMessage(historyNode, "Nenhum retorno registrado desde o início do monitoramento.");
        return;
      }
      historyNode.replaceChildren();
      items.forEach(function (item) {
        const when = formatDateTime(item.created_at);
        const method = String(item.auth_method || "") === "google_oauth" ? "Google" : "Senha";
        appendHistoryLine(historyNode, when, `via ${method}`);
      });
    } catch (_error) {
      setHistoryMessage(historyNode, "Falha de rede ao carregar histórico de logins.");
    }
  }

  if (loginForm) {
    loginForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (!loginBtn) return;
      loginBtn.disabled = true;
      setStatus("Entrando...", null);
      try {
        const formData = new FormData(loginForm);
        const email = String(formData.get("email") || "").trim();
        const password = String(formData.get("password") || "").trim();
        const response = await fetch(`${resolveApiBase()}/admin/auth/login`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ email: email, password: password }),
        });
        const payload = await response.json().catch(function () {
          return {};
        });
        if (!response.ok) {
          setStatus(String(payload.detail || "Login admin inválido."), "error");
          return;
        }
        setAuthenticatedView(true);
        setStatus("Login admin realizado.", "ok");
        await loadDashboard();
      } catch (_error) {
        setStatus("Falha de rede no login admin.", "error");
      } finally {
        loginBtn.disabled = false;
      }
    });
  }

  adminSectionButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      const section = String(button.dataset.adminSection || "dashboard");
      setActiveAdminSection(section);
      if (section === "dashboard") {
        void loadDashboard();
      } else if (section === "orders") {
        void loadOrders();
      } else if (section === "users") {
        void loadUsers();
      }
    });
  });

  if (dashboardRefreshBtn) {
    dashboardRefreshBtn.addEventListener("click", function () {
      void loadDashboard();
    });
  }

  if (dashboardPeriodNode) {
    dashboardPeriodNode.addEventListener("change", function () {
      void loadDashboard();
    });
  }

  if (dashboardIdentityTypeNode) {
    dashboardIdentityTypeNode.addEventListener("change", function () {
      void loadDashboard();
    });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      void loadOrders();
    });
  }

  if (usersRefreshBtn) {
    usersRefreshBtn.addEventListener("click", function () {
      usersOffset = 0;
      void loadUsers();
    });
  }

  if (filterNode) {
    filterNode.addEventListener("change", function () {
      ordersOffset = 0;
      void loadOrders();
    });
  }

  if (queryNode) {
    queryNode.addEventListener("change", function () {
      ordersOffset = 0;
      void loadOrders();
    });
  }

  if (usersQueryNode) {
    usersQueryNode.addEventListener("change", function () {
      usersOffset = 0;
      void loadUsers();
    });
  }

  if (usersFilterNode) {
    usersFilterNode.addEventListener("change", function () {
      usersOffset = 0;
      void loadUsers();
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener("click", function () {
      if (ordersOffset <= 0) return;
      ordersOffset = Math.max(0, ordersOffset - ORDER_PAGE_SIZE);
      void loadOrders();
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener("click", function () {
      if (ordersOffset + ORDER_PAGE_SIZE >= ordersTotal) return;
      ordersOffset += ORDER_PAGE_SIZE;
      void loadOrders();
    });
  }

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async function () {
      try {
        await fetch(`${resolveApiBase()}/admin/auth/logout`, {
          method: "POST",
          credentials: "include",
        });
      } catch (_error) {
      }
      setAuthenticatedView(false);
      setStatus("Sessão encerrada.", "ok");
      setDashboardStatus("", null);
      setUsersStatus("", null);
      if (dashboardSummaryNode) dashboardSummaryNode.replaceChildren();
      if (dashboardDailyChartNode) dashboardDailyChartNode.replaceChildren();
      if (dashboardIdentitiesNode) dashboardIdentitiesNode.replaceChildren();
      if (dashboardTopErrorsNode) dashboardTopErrorsNode.replaceChildren();
      if (dashboardRecentAttentionNode) dashboardRecentAttentionNode.replaceChildren();
      if (ordersListNode) ordersListNode.replaceChildren();
      if (usersListNode) usersListNode.replaceChildren();
    });
  }

  if (ordersListNode) {
    ordersListNode.addEventListener("click", function (event) {
      const target = event.target;
      if (!(target instanceof HTMLButtonElement)) return;
      const action = String(target.dataset.action || "");
      const intentId = String(target.dataset.intentId || "").trim();
      if (!action || !intentId) return;
      if (action === "release") {
        void releaseOrder(intentId);
        return;
      }
      if (action === "send-link") {
        const wrapper = target.closest(".admin-actions");
        const input = wrapper ? wrapper.querySelector("input[data-action='payment-link-input']") : null;
        const paymentLink = input instanceof HTMLInputElement ? input.value : "";
        void sendPaymentLink(intentId, paymentLink);
        return;
      }
      if (action === "history") {
        const wrapper = target.closest(".admin-actions");
        const historyNode = wrapper ? wrapper.querySelector("[data-role='history']") : null;
        if (historyNode instanceof HTMLElement) {
          void loadOrderHistory(intentId, historyNode);
        }
      }
    });
  }

  if (usersListNode) {
    usersListNode.addEventListener("click", function (event) {
      const target = event.target;
      if (!(target instanceof HTMLButtonElement)) return;
      const action = String(target.dataset.action || "");
      const userId = String(target.dataset.userId || "").trim();
      if (!userId) return;
      if (action === "toggle-role") {
        const currentIsAdmin = String(target.dataset.isAdmin || "") === "1";
        void toggleUserRole(userId, currentIsAdmin);
        return;
      }
      if (action === "toggle-status") {
        const currentIsActive = String(target.dataset.isActive || "") === "1";
        void toggleUserStatus(userId, currentIsActive);
        return;
      }
      if (action === "user-role-history") {
        const wrapper = target.closest(".order-card");
        const historyNode = wrapper ? wrapper.querySelector("[data-role='user-role-history']") : null;
        if (historyNode instanceof HTMLElement) {
          void loadUserRoleHistory(userId, historyNode);
        }
        return;
      }
      if (action === "user-login-history") {
        const wrapper = target.closest(".order-card");
        const historyNode = wrapper ? wrapper.querySelector("[data-role='user-login-history']") : null;
        if (historyNode instanceof HTMLElement) {
          void loadUserLoginHistory(userId, historyNode);
        }
      }
    });
  }

  setAuthenticatedView(false);
  updatePager();
  void verifyAdminSession();
})();
