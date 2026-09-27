(function (global) {
  "use strict";

  const LEGACY_TOKEN_KEY = "ofxsimples_user_token";
  const LEGACY_PROFILE_KEY = "ofxsimples_profile_hint";
  const LEGACY_TOKEN_COOKIE = "ofxsimples_user_token";
  const CURRENT_USER_CACHE_TTL_MS = 30 * 1000;
  let migrationPromise = null;
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

  function readLegacyCookie() {
    const entries = String(document.cookie || "").split(";");
    for (const entry of entries) {
      const parts = entry.split("=");
      const name = String(parts.shift() || "").trim();
      if (name !== LEGACY_TOKEN_COOKIE) continue;
      try {
        return decodeURIComponent(parts.join("=").trim());
      } catch (_error) {
        return "";
      }
    }
    return "";
  }

  function readLegacyToken() {
    try {
      return String(localStorage.getItem(LEGACY_TOKEN_KEY) || "").trim() || readLegacyCookie();
    } catch (_error) {
      return readLegacyCookie();
    }
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

  async function migrateLegacySession() {
    const legacyToken = readLegacyToken();
    if (!legacyToken) {
      clearLegacyAuthArtifacts();
      return false;
    }
    try {
      const response = await global.fetch(`${resolveApiBase()}/auth/session/migrate`, {
        method: "POST",
        headers: { authorization: `Bearer ${legacyToken}` },
        credentials: "include",
      });
      if (response.ok || [400, 401, 403].includes(response.status)) {
        clearLegacyAuthArtifacts();
      }
      return response.ok;
    } catch (_error) {
      // Preserve the legacy value until the backend migration endpoint is reachable.
      return false;
    }
  }

  function ready() {
    if (!migrationPromise) migrationPromise = migrateLegacySession();
    return migrationPromise;
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
