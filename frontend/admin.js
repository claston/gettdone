(function () {
  const ADMIN_TOKEN_KEY = "ofxsimples_admin_token";
  const ORDER_PAGE_SIZE = 10;
  const USER_PAGE_SIZE = 20;

  const loginCard = document.getElementById("admin-login-card");
  const panelCard = document.getElementById("admin-panel-card");
  const usersCard = document.getElementById("admin-users-card");
  const loginForm = document.getElementById("admin-login-form");
  const loginBtn = document.getElementById("admin-login-btn");
  const loginStatusNode = document.getElementById("admin-login-status");
  const logoutBtn = document.getElementById("admin-logout-btn");

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

  function resolveApiBase() {
    const host = window.location.hostname;
    const port = window.location.port;
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    const isDevFrontend = isLocalHost && port !== "8000";
    if (isDevFrontend) return "http://127.0.0.1:8000";
    if (window.location.origin && window.location.origin !== "null") return window.location.origin;
    return "http://127.0.0.1:8000";
  }

  function getAdminToken() {
    const token = String(localStorage.getItem(ADMIN_TOKEN_KEY) || "").trim();
    return token || null;
  }

  function saveAdminToken(token) {
    localStorage.setItem(ADMIN_TOKEN_KEY, String(token || "").trim());
  }

  function clearAdminToken() {
    localStorage.removeItem(ADMIN_TOKEN_KEY);
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

  function setAuthenticatedView(isAuthenticated) {
    if (loginCard) loginCard.classList.toggle("hidden", isAuthenticated);
    if (panelCard) panelCard.classList.toggle("hidden", !isAuthenticated);
    if (usersCard) usersCard.classList.toggle("hidden", !isAuthenticated);
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

  function badgeClass(status) {
    const normalized = String(status || "").trim().toUpperCase();
    if (normalized === "AWAITING_PAYMENT") return "badge awaiting";
    if (normalized === "RELEASED_FOR_USE") return "badge released";
    return "badge";
  }

  async function apiRequest(path, init) {
    const token = getAdminToken();
    const headers = Object.assign({}, init?.headers || {});
    if (token) headers.authorization = `Bearer ${token}`;
    const response = await fetch(`${resolveApiBase()}${path}`, Object.assign({}, init || {}, { headers }));
    const payload = await response.json().catch(function () {
      return {};
    });
    return { response, payload };
  }

  async function verifyAdminSession() {
    const token = getAdminToken();
    if (!token) {
      setAuthenticatedView(false);
      return;
    }
    try {
      const { response } = await apiRequest("/admin/me");
      if (!response.ok) {
        clearAdminToken();
        setAuthenticatedView(false);
        return;
      }
      setAuthenticatedView(true);
      await Promise.all([loadOrders(), loadUsers()]);
    } catch (_error) {
      setStatus("Falha de rede ao validar sessao admin.", "error");
      setAuthenticatedView(false);
    }
  }

  function updatePager() {
    if (pageLabelNode) {
      const currentPage = Math.floor(ordersOffset / ORDER_PAGE_SIZE) + 1;
      const totalPages = Math.max(1, Math.ceil(ordersTotal / ORDER_PAGE_SIZE));
      pageLabelNode.textContent = `Pagina ${currentPage} de ${totalPages}`;
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
          clearAdminToken();
          setAuthenticatedView(false);
        }
        setStatus(String(payload.detail || "Nao foi possivel carregar os pedidos."), "error");
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
    const onlyAdminParam =
      rawFilter === "admin" ? "&only_admin=true" : rawFilter === "non_admin" ? "&only_admin=false" : "";
    setUsersStatus("Carregando usuarios...", null);
    try {
      const { response, payload } = await apiRequest(
        `/admin/users?query=${encodeURIComponent(query)}${onlyAdminParam}&limit=${USER_PAGE_SIZE}&offset=${usersOffset}`,
      );
      if (!response.ok) {
        setUsersStatus(String(payload.detail || "Nao foi possivel carregar usuarios."), "error");
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      renderUsers(items);
      setUsersStatus(`Usuarios carregados: ${items.length} de ${Number(payload.total || 0)}.`, "ok");
    } catch (_error) {
      setUsersStatus("Falha de rede ao carregar usuarios.", "error");
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

    container.innerHTML = [
      `<div class="order-head">`,
      `  <div>`,
      `    <h3 class="order-title">Protocolo ${String(order.intent_id || "-")}</h3>`,
      `    <p class="order-meta">Criado em ${formatDateTime(order.created_at)}</p>`,
      `  </div>`,
      `  <span class="${badgeClass(status)}">${mapStatusLabel(status)}</span>`,
      `</div>`,
      `<div class="grid">`,
      `  <p><strong>Plano:</strong> ${String(order.plan_name || "-")}</p>`,
      `  <p><strong>Valor:</strong> ${formatPriceBRL(order.price_cents)}</p>`,
      `  <p><strong>Cliente:</strong> ${String(order.customer_name || "-")}</p>`,
      `  <p><strong>Email:</strong> ${String(order.customer_email || "-")}</p>`,
      `  <p><strong>WhatsApp:</strong> ${String(order.customer_whatsapp || "-")}</p>`,
      `  <p><strong>User ID:</strong> ${String(order.user_id || "-")}</p>`,
      `  <p><strong>Proximo passo:</strong> ${mapNextStepLabel(order.next_step)}</p>`,
      `  <p><strong>Link atual:</strong> ${String(order.payment_link || "-")}</p>`,
      `</div>`,
      `<div class="admin-actions">`,
      `  <div class="inline">`,
      `    <input data-action="payment-link-input" type="url" placeholder="https://pagamento.exemplo/link" value="${String(order.payment_link || "")}" />`,
      `    <button data-action="send-link" data-intent-id="${String(order.intent_id || "")}" ${isReleased ? "disabled" : ""}>Enviar link</button>`,
      `  </div>`,
      `  <div class="pill-row">`,
      `    <button data-action="release" data-intent-id="${String(order.intent_id || "")}" class="ghost" ${
        canRelease ? "" : "disabled"
      }>Liberar plano</button>`,
      `    <button data-action="history" data-intent-id="${String(order.intent_id || "")}" class="ghost">Ver historico</button>`,
      `  </div>`,
      `  <div class="history hidden" data-role="history"></div>`,
      `</div>`,
    ].join("");
    return container;
  }

  function renderOrders(items) {
    if (!ordersListNode || !emptyNode) return;
    ordersListNode.innerHTML = "";
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
    usersListNode.innerHTML = "";
    items.forEach(function (user) {
      const isAdmin = !!user.is_admin;
      const card = document.createElement("article");
      card.className = "order-card";
      card.innerHTML = [
        `<div class="order-head">`,
        `  <div>`,
        `    <h3 class="order-title">${String(user.name || "-")}</h3>`,
        `    <p class="order-meta">${String(user.email || "-")}</p>`,
        `  </div>`,
        `  <span class="badge ${isAdmin ? "released" : ""}">${isAdmin ? "Admin" : "Usuario"}</span>`,
        `</div>`,
        `<div class="grid">`,
        `  <p><strong>User ID:</strong> ${String(user.user_id || "-")}</p>`,
        `  <p><strong>Criado:</strong> ${formatDateTime(user.created_at)}</p>`,
        `  <p><strong>Atualizado:</strong> ${formatDateTime(user.updated_at)}</p>`,
        `</div>`,
        `<div class="pill-row">`,
        `  <button data-action="toggle-role" data-user-id="${String(user.user_id || "")}" data-is-admin="${isAdmin ? "1" : "0"}" class="ghost">${
          isAdmin ? "Revogar admin" : "Promover a admin"
        }</button>`,
        `  <button data-action="user-role-history" data-user-id="${String(user.user_id || "")}" class="ghost">Ver historico de acesso</button>`,
        `</div>`,
        `<div class="history hidden" data-role="user-history"></div>`,
      ].join("");
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
        setStatus(String(payload.detail || "Nao foi possivel salvar o link de pagamento."), "error");
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
        setStatus(String(payload.detail || "Nao foi possivel liberar o plano."), "error");
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
    historyNode.innerHTML = "<p>Carregando historico...</p>";
    try {
      const { response, payload } = await apiRequest(
        `/admin/checkout/intents/${encodeURIComponent(intentId)}/history?limit=20`,
      );
      if (!response.ok) {
        historyNode.innerHTML = `<p>${String(payload.detail || "Falha ao carregar historico.")}</p>`;
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        historyNode.innerHTML = "<p>Nenhum evento registrado.</p>";
        return;
      }
      historyNode.innerHTML = items
        .map(function (item) {
          const when = formatDateTime(item.created_at);
          const who = String(item.actor_kind || "system");
          const msg = String(item.event_message || item.event_type || "-");
          return `<p><strong>${when}</strong> [${who}] ${msg}</p>`;
        })
        .join("");
    } catch (_error) {
      historyNode.innerHTML = "<p>Falha de rede ao carregar historico.</p>";
    }
  }

  async function toggleUserRole(userId, currentIsAdmin) {
    const targetState = !currentIsAdmin;
    setUsersStatus(targetState ? "Promovendo usuario..." : "Revogando acesso admin...", null);
    try {
      const { response, payload } = await apiRequest("/admin/users/role", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ user_id: userId, is_admin: targetState }),
      });
      if (!response.ok) {
        setUsersStatus(String(payload.detail || "Nao foi possivel atualizar o acesso."), "error");
        return;
      }
      setUsersStatus("Acesso atualizado.", "ok");
      await loadUsers();
    } catch (_error) {
      setUsersStatus("Falha de rede ao atualizar acesso.", "error");
    }
  }

  async function loadUserRoleHistory(userId, historyNode) {
    if (!historyNode) return;
    historyNode.classList.remove("hidden");
    historyNode.innerHTML = "<p>Carregando historico...</p>";
    try {
      const { response, payload } = await apiRequest(
        `/admin/users/${encodeURIComponent(userId)}/history?limit=20`,
      );
      if (!response.ok) {
        historyNode.innerHTML = `<p>${String(payload.detail || "Falha ao carregar historico.")}</p>`;
        return;
      }
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (!items.length) {
        historyNode.innerHTML = "<p>Nenhuma alteracao registrada.</p>";
        return;
      }
      historyNode.innerHTML = items
        .map(function (item) {
          const when = formatDateTime(item.created_at);
          const actor = String(item.actor_email || item.actor_user_id || "sistema");
          const label = String(item.new_is_admin ? "Promovido para admin" : "Revogado admin");
          return `<p><strong>${when}</strong> ${label} por ${actor}</p>`;
        })
        .join("");
    } catch (_error) {
      historyNode.innerHTML = "<p>Falha de rede ao carregar historico.</p>";
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
          body: JSON.stringify({ email: email, password: password }),
        });
        const payload = await response.json().catch(function () {
          return {};
        });
        if (!response.ok) {
          setStatus(String(payload.detail || "Login admin invalido."), "error");
          return;
        }
        saveAdminToken(payload.admin_token);
        setAuthenticatedView(true);
        setStatus("Login admin realizado.", "ok");
        await Promise.all([loadOrders(), loadUsers()]);
      } catch (_error) {
        setStatus("Falha de rede no login admin.", "error");
      } finally {
        loginBtn.disabled = false;
      }
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
    logoutBtn.addEventListener("click", function () {
      clearAdminToken();
      setAuthenticatedView(false);
      setStatus("Sessao encerrada.", "ok");
      setUsersStatus("", null);
      if (ordersListNode) ordersListNode.innerHTML = "";
      if (usersListNode) usersListNode.innerHTML = "";
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
      if (action === "user-role-history") {
        const wrapper = target.closest(".order-card");
        const historyNode = wrapper ? wrapper.querySelector("[data-role='user-history']") : null;
        if (historyNode instanceof HTMLElement) {
          void loadUserRoleHistory(userId, historyNode);
        }
      }
    });
  }

  setAuthenticatedView(false);
  updatePager();
  void verifyAdminSession();
})();
