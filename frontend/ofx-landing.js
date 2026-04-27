(function () {
  const yearNode = document.getElementById("footer-year");
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");
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
    const raw = localStorage.getItem("gettdone_user_token");
    const token = String(raw || "").trim();
    return token || null;
  }

  function clearUserToken() {
    localStorage.removeItem("gettdone_user_token");
  }

  function applyLoggedInTopState() {
    if (topAuthLoginLink) topAuthLoginLink.classList.add("hidden");
    if (topAuthPrimaryLink) {
      topAuthPrimaryLink.textContent = "Minha Conta";
      topAuthPrimaryLink.setAttribute("href", "./client-area.html");
    }
  }

  function applyLoggedOutTopState() {
    if (topAuthLoginLink) {
      topAuthLoginLink.classList.remove("hidden");
      topAuthLoginLink.setAttribute("href", "./login.html?next=%2Fofx-convert.html");
    }
    if (topAuthPrimaryLink) {
      topAuthPrimaryLink.textContent = "Converter agora";
      topAuthPrimaryLink.setAttribute("href", "./ofx-convert.html");
    }
  }

  async function validateSessionToken(token) {
    if (!token) return false;
    try {
      const apiBase = resolveApiBase();
      const response = await fetch(`${apiBase}/auth/me?user_token=${encodeURIComponent(token)}`);
      return response.ok;
    } catch (_error) {
      return false;
    }
  }

  async function syncTopAuthBySession() {
    const token = getUserToken();
    if (!token) {
      applyLoggedOutTopState();
      return;
    }
    const valid = await validateSessionToken(token);
    if (valid) {
      applyLoggedInTopState();
      return;
    }
    clearUserToken();
    applyLoggedOutTopState();
  }

  const menuToggle = document.getElementById("menu-toggle");
  const topLinks = document.getElementById("top-links");
  if (menuToggle && topLinks) {
    menuToggle.addEventListener("click", function () {
      const open = topLinks.classList.toggle("is-open");
      menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  void syncTopAuthBySession();
})();
