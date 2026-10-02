(function () {
  const yearNode = document.getElementById("footer-year");
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");
  const pricingGrid = document.getElementById("pricing-grid");
  const menuToggle = document.getElementById("menu-toggle");
  const topLinks = document.getElementById("top-links");
  const session = window.OfxSession;
  const RETRYABLE_STATUS = new Set([408, 425, 429, 500, 502, 503, 504]);
  const COLD_START_TIMEOUT_MS = 5000;

  if (yearNode) {
    yearNode.textContent = "(c) " + new Date().getFullYear() + " OFX Simples. Todos os direitos reservados.";
  }

  function renderLoggedInTop(email) {
    if (topAuthLoginLink) topAuthLoginLink.classList.add("hidden");
    if (topAuthPrimaryLink) {
      const safe = String(email || "conta").trim() || "conta";
      const initial = safe.charAt(0).toUpperCase();
      const avatar = document.createElement("span");
      avatar.className = "top-account-avatar";
      avatar.textContent = initial;
      const label = document.createElement("span");
      label.className = "top-account-email";
      label.textContent = safe;
      const caret = document.createElement("span");
      caret.className = "top-account-caret";
      caret.textContent = "▾";
      topAuthPrimaryLink.replaceChildren(avatar, label, caret);
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

  function sleep(ms) {
    return new Promise(function (resolve) {
      window.setTimeout(resolve, ms);
    });
  }

  function isRetryableStatus(statusCode) {
    return RETRYABLE_STATUS.has(Number(statusCode || 0));
  }

  async function ensureTelemetryIdentity() {
    if (!session) return false;
    await session.ready();
    const currentUser = await session.getCurrentUser().catch(function () {
      return null;
    });
    if (currentUser) return true;
    const response = await fetch(`${session.apiBase}/auth/anonymous-session`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "include",
      body: "{}",
    });
    return response.ok;
  }

  async function recordProductEvent(eventType, planCode) {
    try {
      if (!(await ensureTelemetryIdentity())) return;
      await fetch(`${session.apiBase}/telemetry/events`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        keepalive: true,
        body: JSON.stringify({
          event_type: eventType,
          page_path: "/planos.html",
          plan_code: planCode || null,
        }),
      });
    } catch (_error) {
      // Telemetria nunca deve impedir a navegação ou a compra.
    }
  }

  async function fetchJsonWithRetry(url, init, attempts) {
    const maxAttempts = Math.max(1, Number(attempts || 1));
    let lastError = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const controller = typeof AbortController === "function" ? new AbortController() : null;
      const timeoutHandle = controller
        ? window.setTimeout(function () {
            controller.abort();
          }, COLD_START_TIMEOUT_MS)
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
          return payload;
        }
        if (attempt < maxAttempts && isRetryableStatus(response.status)) {
          await sleep(450 * attempt);
          continue;
        }
        throw new Error("catalog-unavailable");
      } catch (error) {
        lastError = error;
        if (attempt < maxAttempts) {
          await sleep(450 * attempt);
          continue;
        }
      } finally {
        if (timeoutHandle !== null) {
          window.clearTimeout(timeoutHandle);
        }
      }
    }
    throw lastError || new Error("catalog-unavailable");
  }

  function renderPlans(items) {
    if (!pricingGrid) return;
    const plans = Array.isArray(items) ? items.slice() : [];
    if (!plans.length) {
      pricingGrid.innerHTML = [
        '<article class="plan-card">',
        "<h2>Planos indisponíveis</h2>",
        '<p class="price">Consulte suporte</p>',
        "<ul><li>Tente novamente em instantes</li></ul>",
        '<a class="btn btn-outline" data-product-event="plan_cta_click" href="./checkout.html">Ir para checkout</a>',
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
          `<li>Tamanho máximo: ${Math.round(Number(plan.max_upload_size_bytes || 0) / (1024 * 1024))} MB por arquivo</li>`,
          "<li>Suporte por contato</li>",
          "</ul>",
          `<a class="${ctaClass}" data-product-event="plan_cta_click" data-plan-code="${code}" href="./checkout.html?plan=${encodeURIComponent(code)}">Quero este plano</a>`,
          "</article>",
        ].join("");
      })
      .join("");
  }

  async function loadPlansCatalog() {
    if (!pricingGrid) return;
    try {
      const apiBase = session ? session.apiBase : window.location.origin;
      const payload = await fetchJsonWithRetry(`${apiBase}/plans`, undefined, 3);
      renderPlans(payload.items || []);
    } catch (_error) {
      renderPlans([]);
    }
  }

  async function syncTopAuthBySession() {
    try {
      const currentUser = session ? await session.getCurrentUser() : null;
      if (currentUser) renderLoggedInTop(currentUser.email);
      else renderLoggedOutTop();
    } catch (_error) {
      renderLoggedOutTop();
    }
  }

  if (menuToggle && topLinks) {
    menuToggle.addEventListener("click", function () {
      const open = topLinks.classList.toggle("is-open");
      menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  document.addEventListener("click", function (event) {
    const target = event.target instanceof Element ? event.target.closest('[data-product-event="plan_cta_click"]') : null;
    if (!target) return;
    void recordProductEvent("plan_cta_click", String(target.getAttribute("data-plan-code") || ""));
  });

  void loadPlansCatalog();
  void syncTopAuthBySession();
  void recordProductEvent("plans_view", null);
})();
