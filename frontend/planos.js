(function () {
  const yearNode = document.getElementById("footer-year");
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");
  const pricingGrid = document.getElementById("pricing-grid");
  const menuToggle = document.getElementById("menu-toggle");
  const topLinks = document.getElementById("top-links");
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const PROFILE_HINT_KEY = "ofxsimples_profile_hint";

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
      topAuthPrimaryLink.innerHTML = `<span class="top-account-avatar">${initial}</span><span class="top-account-email">${safe}</span><span class="top-account-caret">▾</span>`;
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

  function formatPriceBRL(priceCents) {
    const amount = Number(priceCents || 0) / 100;
    return amount.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function renderPlans(items) {
    if (!pricingGrid) return;
    const plans = Array.isArray(items) ? items.slice() : [];
    if (!plans.length) {
      pricingGrid.innerHTML = [
        '<article class="plan-card">',
        "<h2>Planos indisponiveis</h2>",
        '<p class="price">Consulte suporte</p>',
        "<ul><li>Tente novamente em instantes</li></ul>",
        '<a class="btn btn-outline" href="./checkout.html">Ir para checkout</a>',
        "</article>",
      ].join("");
      return;
    }

    plans.sort(function (a, b) {
      return Number(a.price_cents || 0) - Number(b.price_cents || 0);
    });
    const featuredCode = "profissional";

    pricingGrid.innerHTML = plans
      .map(function (plan) {
        const code = String(plan.code || "").toLowerCase();
        const isFeatured = code === featuredCode;
        const cardClass = isFeatured ? "plan-card plan-card-featured" : "plan-card";
        const ctaClass = isFeatured ? "btn btn-primary" : "btn btn-outline";
        return [
          `<article class="${cardClass}">`,
          isFeatured ? '<p class="badge">Mais escolhido</p>' : "",
          `<h2>${String(plan.name || "")}</h2>`,
          `<p class="price">${formatPriceBRL(plan.price_cents)}<span>/mês</span></p>`,
          "<ul>",
          `<li>${Number(plan.quota_limit || 0)} páginas por mês</li>`,
          `<li>Até ${Number(plan.max_pages_per_file || 0)} páginas por arquivo</li>`,
          `<li>Tamanho máximo: ${Math.round(Number(plan.max_upload_size_bytes || 0) / (1024 * 1024))} MB por arquivo</li>`,
          "<li>Suporte por contato</li>",
          "</ul>",
          `<a class="${ctaClass}" href="./checkout.html?plan=${encodeURIComponent(code)}">Quero este plano</a>`,
          "</article>",
        ].join("");
      })
      .join("");
  }

  async function loadPlansCatalog() {
    if (!pricingGrid) return;
    try {
      const apiBase = resolveApiBase();
      const response = await fetch(`${apiBase}/plans`);
      if (!response.ok) throw new Error("catalog-unavailable");
      const payload = await response.json().catch(function () {
        return {};
      });
      renderPlans(payload.items || []);
    } catch (_error) {
      renderPlans([]);
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
      const payload = await response.json().catch(() => ({}));
      const email = String(payload.email || "").trim();
      if (email) {
        setProfileHint(email);
        renderLoggedInTop(email);
      }
    } catch (_error) {
      // Keep optimistic state.
    }
  }

  if (menuToggle && topLinks) {
    menuToggle.addEventListener("click", function () {
      const open = topLinks.classList.toggle("is-open");
      menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  void loadPlansCatalog();
  void syncTopAuthBySession();
})();
