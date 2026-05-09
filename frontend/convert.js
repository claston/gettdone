(function () {
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");
  const menuToggle = document.getElementById("menu-toggle");
  const topLinks = document.getElementById("top-links");
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const PROFILE_HINT_KEY = "ofxsimples_profile_hint";

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
    return String(localStorage.getItem(USER_TOKEN_KEY) || "").trim() || null;
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
      topAuthPrimaryLink.innerHTML = `<span class="top-account-avatar">${initial}</span><span class="top-account-email">${safe}</span><span class="top-account-caret">&#9662;</span>`;
      topAuthPrimaryLink.classList.add("top-account-trigger");
      topAuthPrimaryLink.setAttribute("href", "/client-area.html");
    }
  }

  function renderLoggedOutTop() {
    if (topAuthLoginLink) {
      topAuthLoginLink.classList.remove("hidden");
      topAuthLoginLink.setAttribute("href", "/login.html?next=%2Fconvert.html");
    }
    if (topAuthPrimaryLink) {
      topAuthPrimaryLink.textContent = "Converter agora";
      topAuthPrimaryLink.classList.remove("top-account-trigger");
      topAuthPrimaryLink.setAttribute("href", "/convert.html");
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
      const response = await fetch(`${apiBase}/auth/me`, {
        headers: { authorization: `Bearer ${token}` },
      });
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

  void syncTopAuthBySession();
})();
