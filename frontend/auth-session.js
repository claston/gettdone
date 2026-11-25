(function (global) {
  "use strict";

  const LEGACY_TOKEN_KEY = "ofxsimples_user_token";
  const LEGACY_PROFILE_KEY = "ofxsimples_profile_hint";
  const LEGACY_TOKEN_COOKIE = "ofxsimples_user_token";
  const CURRENT_USER_CACHE_TTL_MS = 30 * 1000;
  let refreshPromise = null;
  let currentUserPromise = null;
  let currentUserCacheExpiresAt = 0;

  function clearCurrentUserCache() {
    currentUserPromise = null;
    currentUserCacheExpiresAt = 0;
  }

  function resolveApiBase() {
    const host = global.location.hostname;
    const port = global.location.port;
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    const isDevFrontend = isLocalHost && port !== "8000";
    if (isDevFrontend) return "http://127.0.0.1:8000";
    if (global.location.origin && global.location.origin !== "null") return global.location.origin;
    return "http://127.0.0.1:8000";
  }

  function legacyCookieDomains() {
    const host = String(global.location.hostname || "").trim().toLowerCase();
    if (host === "ofxsimples.com.br" || host.endsWith(".ofxsimples.com.br")) {
      return [".ofxsimples.com.br"];
    }
    return [];
  }

  function clearLegacyAuthArtifacts() {
    try {
      localStorage.removeItem(LEGACY_TOKEN_KEY);
      localStorage.removeItem(LEGACY_PROFILE_KEY);
    } catch (_error) {
      // Storage can be unavailable in privacy-restricted browser contexts.
    }
    const secure = global.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${LEGACY_TOKEN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax${secure}`;
    for (const domain of legacyCookieDomains()) {
      document.cookie = `${LEGACY_TOKEN_COOKIE}=; Path=/; Max-Age=0; Domain=${domain}; SameSite=Lax${secure}`;
    }
  }

  async function ready() {
    clearLegacyAuthArtifacts();
  }

  async function refreshSession() {
    if (!refreshPromise) {
      refreshPromise = global
        .fetch(`${resolveApiBase()}/auth/session/refresh`, {
          method: "POST",
          credentials: "include",
        })
        .then(function (response) {
          return response.ok;
        })
        .catch(function () {
          return false;
        })
        .finally(function () {
          refreshPromise = null;
        });
    }
    return refreshPromise;
  }

  async function request(input, init, options) {
    await ready();
    const settings = options || {};
    const requestInit = Object.assign({}, init || {}, { credentials: "include" });
    let response = await global.fetch(input, requestInit);
    if (response.status === 401 && settings.refresh !== false && (await refreshSession())) {
      response = await global.fetch(input, requestInit);
    }
    return response;
  }

  async function getCurrentUser() {
    const now = Date.now();
    if (currentUserPromise && currentUserCacheExpiresAt > now) {
      return currentUserPromise;
    }
    currentUserCacheExpiresAt = now + CURRENT_USER_CACHE_TTL_MS;
    currentUserPromise = request(`${resolveApiBase()}/auth/me`)
      .then(function (response) {
        if (!response.ok) return null;
        return response.json().catch(function () {
          return null;
        });
      })
      .catch(function (error) {
        clearCurrentUserCache();
        throw error;
      });
    return currentUserPromise;
  }

  async function login(payload) {
    clearCurrentUserCache();
    await ready();
    return global.fetch(`${resolveApiBase()}/auth/session/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
    });
  }

  async function logout() {
    clearCurrentUserCache();
    await ready();
    try {
      return await global.fetch(`${resolveApiBase()}/auth/session/logout`, {
        method: "POST",
        credentials: "include",
      });
    } finally {
      clearLegacyAuthArtifacts();
    }
  }

  global.OfxSession = Object.freeze({
    get apiBase() {
      return resolveApiBase();
    },
    getCurrentUser,
    login,
    logout,
    ready,
    refresh: refreshSession,
    request,
  });
})(window);
