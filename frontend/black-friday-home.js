(function () {
  "use strict";

  const banner = document.getElementById("black-friday-banner");
  const badge = document.getElementById("black-friday-nav-badge");
  const price = document.getElementById("black-friday-home-price");
  if (!banner || !badge || !price) return;

  const start = Date.parse("2026-11-23T00:00:00-03:00");
  const end = Date.parse("2026-12-01T00:00:00-03:00");
  const monthlyOffers = new Set(["essencial_black", "profissional_black"]);
  const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  let activeRequest;
  let expiryTimer;

  function inCampaign() {
    return Date.now() >= start && Date.now() < end;
  }

  function hidePromotion() {
    banner.hidden = true;
    badge.hidden = true;
  }

  async function refreshPromotion() {
    hidePromotion();
    window.clearTimeout(expiryTimer);
    if (activeRequest) activeRequest.abort();
    if (!inCampaign()) return;

    const controller = new AbortController();
    activeRequest = controller;
    const timeout = window.setTimeout(function () { controller.abort(); }, 6000);
    try {
      const apiBase = window.OfxSession ? window.OfxSession.apiBase : window.location.origin;
      const response = await fetch(`${apiBase}/plans`, {
        signal: controller.signal,
        cache: "no-store",
        credentials: "omit",
      });
      if (!response.ok) return;
      const catalog = await response.json();
      if (!Array.isArray(catalog.items) || activeRequest !== controller || !inCampaign()) return;
      const amounts = catalog.items.filter(function (plan) {
        return plan && monthlyOffers.has(plan.code) && plan.billing_period === "monthly"
          && plan.currency === "BRL" && Number.isSafeInteger(plan.price_cents) && plan.price_cents > 0;
      }).map(function (plan) { return plan.price_cents; });
      if (!amounts.length) return;

      price.textContent = money.format(Math.min(...amounts) / 100);
      banner.hidden = false;
      badge.hidden = false;
      expiryTimer = window.setTimeout(hidePromotion, end - Date.now());
    } catch (_error) {
      // An unavailable catalog must not advertise an unconfirmed offer.
    } finally {
      window.clearTimeout(timeout);
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) void refreshPromotion();
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) void refreshPromotion();
  });
  void refreshPromotion();
})();
