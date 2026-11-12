(function () {
  "use strict";

  function create({ containerId, linkId, apiBase, enabled = false, message = "" }) {
    const container = document.getElementById(containerId);
    const link = document.getElementById(linkId);
    let number = null;

    function render() {
      if (!container || !link) return;
      const visible = enabled && number && message;
      if (visible) {
        link.href = `https://wa.me/${number}?text=${encodeURIComponent(message)}`;
      } else {
        link.removeAttribute("href");
      }
      container.classList.toggle("hidden", !visible);
    }

    async function load() {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 5000);
        try {
          const response = await fetch(`${apiBase}/public-config`, { cache: "no-store", signal: controller.signal });
          if (!response.ok) continue;
          const payload = await response.json();
          const value = payload && payload.whatsapp_support_number;
          number = typeof value === "string" && /^[1-9][0-9]{7,14}$/.test(value) ? value : null;
          render();
          return;
        } catch (_error) {
          // O contato é opcional e permanece oculto se a configuração estiver indisponível.
        } finally {
          window.clearTimeout(timer);
        }
      }
    }

    render();
    if (container && link) void load();
    return {
      update(options) {
        enabled = options.enabled === true;
        message = options.message || "";
        render();
      },
    };
  }

  window.OfxWhatsApp = Object.freeze({ create });
})();
