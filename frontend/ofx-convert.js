(function () {
  const input = document.getElementById("file-input");
  const dropzone = document.getElementById("dropzone");
  const dropzoneEmpty = document.getElementById("dropzone-empty");
  const dropzoneLoaded = document.getElementById("dropzone-loaded");
  const dropzoneFileMeta = document.getElementById("dropzone-file-meta");
  const clearFileBtn = document.getElementById("clear-file-btn");
  const selectedFile = document.getElementById("selected-file");
  const convertBtn = document.getElementById("convert-btn");
  const statusMsg = document.getElementById("status-msg");
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");
  const menuToggle = document.getElementById("menu-toggle");
  const topLinks = document.getElementById("top-links");
  const quotaLockOverlay = document.getElementById("quota-lock-overlay");
  const quotaLockMessage = document.getElementById("quota-lock-message");
  const quotaLockSignupLink = document.getElementById("quota-lock-signup-link");
  const quotaLockLoginLink = document.getElementById("quota-lock-login-link");
  const uploadLimitsText = document.getElementById("upload-limits-text");

  const reviewSection = document.getElementById("review-section");
  const downloadSection = document.getElementById("download-section");
  const reviewRows = document.getElementById("review-rows");
  const kpis = document.getElementById("kpis");
  const addRowBtn = document.getElementById("add-row-btn");

  const analysisIdNode = document.getElementById("analysis-id");
  const processingIdNode = document.getElementById("processing-id");
  const quotaRemainingNode = document.getElementById("quota-remaining");
  const downloadOfxBtn = document.getElementById("download-ofx-btn");
  const VIEW_STATE_KEY = "ofxsimples_ofx_convert_view_state_v1";

  const state = {
    analysisId: null,
    processingId: null,
    isLoading: false,
    restoredFileMeta: null,
    previewRows: [],
    originalRows: [],
    editingRowId: null,
    editDraft: null,
    analysisSnapshot: null,
    lastChangedRowId: null,
    lastChangedRowKind: null,
    rowHighlightTimer: null,
    quotaMode: "conversion",
  };

  function isDraftRowId(rowId) {
    return String(rowId || "").startsWith("row_draft_");
  }

  function resolveApiBase() {
    const host = window.location.hostname;
    const port = window.location.port;
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    const isDevFrontend = isLocalHost && port !== "8000";
    if (isDevFrontend) {
      return "http://127.0.0.1:8000";
    }
    if (window.location.origin && window.location.origin !== "null") {
      return window.location.origin;
    }
    return "http://127.0.0.1:8000";
  }

  const apiBase = resolveApiBase();
  const QUOTA_SIGNUP_URL = "./signup.html?next=%2Fclient-area.html&reason=quota";
  const QUOTA_LOGIN_URL = "./login.html?next=%2Fclient-area.html&force_auth=1";
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const USER_TOKEN_COOKIE = "ofxsimples_user_token";
  const TOKEN_SHARED_COOKIE_ALLOWLIST = ["ofxsimples.com.br"];
  const PROFILE_HINT_KEY = "ofxsimples_profile_hint";
  const ANON_FINGERPRINT_KEY = "ofxsimples_anon_fingerprint";

  function isIpv4Host(hostname) {
    return /^\d{1,3}(\.\d{1,3}){3}$/.test(String(hostname || "").trim());
  }

  function normalizeDomainCandidate(value) {
    return String(value || "").trim().toLowerCase().replace(/^\.+/, "");
  }

  function getConfiguredSharedCookieAllowlist() {
    const configured = window.__OFX_TOKEN_SHARED_COOKIE_ALLOWLIST__;
    if (!Array.isArray(configured)) {
      return TOKEN_SHARED_COOKIE_ALLOWLIST;
    }
    const normalized = configured
      .map(function (item) {
        return normalizeDomainCandidate(item);
      })
      .filter(function (item) {
        return /^[a-z0-9.-]+$/.test(item) && item.includes(".");
      });
    if (normalized.length) {
      return normalized;
    }
    return TOKEN_SHARED_COOKIE_ALLOWLIST;
  }

  function resolveLegacySharedCookieDomain() {
    const host = String(window.location.hostname || "").trim().toLowerCase();
    if (!host || host === "localhost" || isIpv4Host(host)) {
      return null;
    }
    const labels = host.split(".").filter(Boolean);
    if (labels.length < 2) {
      return null;
    }
    if (labels.length >= 3 && labels[labels.length - 2] === "com" && labels[labels.length - 1] === "br") {
      return `.${labels.slice(-3).join(".")}`;
    }
    return `.${labels.slice(-2).join(".")}`;
  }

  function resolveSharedCookieDomain() {
    if (window.location.protocol !== "https:") {
      return null;
    }
    const host = String(window.location.hostname || "").trim().toLowerCase();
    if (!host || host === "localhost" || isIpv4Host(host)) {
      return null;
    }
    const allowedDomains = getConfiguredSharedCookieAllowlist();
    for (const allowedDomain of allowedDomains) {
      if (host === allowedDomain || host.endsWith(`.${allowedDomain}`)) {
        return `.${allowedDomain}`;
      }
    }
    return null;
  }

  function readUserTokenCookie() {
    const entries = String(document.cookie || "").split(";");
    for (const entry of entries) {
      const [namePart, ...valueParts] = entry.split("=");
      const name = String(namePart || "").trim();
      if (name !== USER_TOKEN_COOKIE) continue;
      const rawValue = valueParts.join("=");
      const decoded = decodeURIComponent(String(rawValue || "").trim());
      if (decoded) return decoded;
    }
    return "";
  }

  function writeUserTokenCookie(token) {
    const safeToken = encodeURIComponent(String(token || "").trim());
    if (!safeToken) return;
    const secureAttr = window.location.protocol === "https:" ? "; Secure" : "";
    const sharedDomain = resolveSharedCookieDomain();
    document.cookie = `${USER_TOKEN_COOKIE}=${safeToken}; Path=/; Max-Age=2592000; SameSite=Lax${secureAttr}`;
    if (sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=${safeToken}; Path=/; Max-Age=2592000; Domain=${sharedDomain}; SameSite=Lax${secureAttr}`;
    }
  }

  function clearUserTokenCookie() {
    const secureAttr = window.location.protocol === "https:" ? "; Secure" : "";
    const sharedDomain = resolveSharedCookieDomain();
    const legacySharedDomain = resolveLegacySharedCookieDomain();
    document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax${secureAttr}`;
    if (sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; Domain=${sharedDomain}; SameSite=Lax${secureAttr}`;
    }
    if (legacySharedDomain && legacySharedDomain !== sharedDomain) {
      document.cookie = `${USER_TOKEN_COOKIE}=; Path=/; Max-Age=0; Domain=${legacySharedDomain}; SameSite=Lax${secureAttr}`;
    }
  }

  function getAnonymousFingerprint() {
    const existing = String(localStorage.getItem(ANON_FINGERPRINT_KEY) || "").trim();
    if (existing) {
      return existing;
    }
    const generated = `anon-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    localStorage.setItem(ANON_FINGERPRINT_KEY, generated);
    return generated;
  }

  function getUserToken() {
    const localToken = String(localStorage.getItem(USER_TOKEN_KEY) || "").trim();
    if (localToken) {
      writeUserTokenCookie(localToken);
      return localToken;
    }
    const cookieToken = readUserTokenCookie();
    if (cookieToken) {
      localStorage.setItem(USER_TOKEN_KEY, cookieToken);
      return cookieToken;
    }
    return null;
  }

  function clearUserToken() {
    localStorage.removeItem(USER_TOKEN_KEY);
    clearUserTokenCookie();
  }

  function getProfileHint() {
    return String(localStorage.getItem(PROFILE_HINT_KEY) || "").trim() || "conta";
  }

  function setProfileHint(email) {
    const value = String(email || "").trim();
    if (value) {
      localStorage.setItem(PROFILE_HINT_KEY, value);
    }
    const token = getUserToken();
    if (token) {
      writeUserTokenCookie(token);
    }
  }

  function consumeLogoutQueryFlag() {
    const url = new URL(window.location.href);
    const rawLogout = String(url.searchParams.get("logout") || "").trim().toLowerCase();
    const shouldLogout = rawLogout === "1" || rawLogout === "true" || rawLogout === "yes" || rawLogout === "on";
    if (!shouldLogout) {
      return false;
    }
    clearUserToken();
    url.searchParams.delete("logout");
    const cleaned = `${url.pathname}${url.search}${url.hash}`;
    window.history.replaceState({}, "", cleaned);
    return true;
  }

  function syncQuotaAuthLinks() {
    if (quotaLockSignupLink) {
      quotaLockSignupLink.setAttribute("href", QUOTA_SIGNUP_URL);
    }
    if (quotaLockLoginLink) {
      quotaLockLoginLink.setAttribute("href", QUOTA_LOGIN_URL);
    }
  }

  async function getSessionValidationState() {
    const token = getUserToken();
    try {
      const requestInit = {
        credentials: "include",
      };
      if (token) {
        requestInit.headers = { authorization: `Bearer ${token}` };
      }
      const response = await fetch(`${apiBase}/auth/me`, {
        ...requestInit,
      });
      if (response.ok) {
        return "valid";
      }
      if (response.status === 401) {
        return token ? "invalid" : "missing";
      }
      return "unknown";
    } catch (_error) {
      return "unknown";
    }
  }

  function buildOptionalAuthHeaders(userToken) {
    const token = String(userToken || "").trim();
    if (!token) {
      return null;
    }
    return { authorization: `Bearer ${token}` };
  }

  function buildIdentityQueryParams() {
    const params = new URLSearchParams();
    params.set("anonymous_fingerprint", getAnonymousFingerprint());
    return params;
  }

  function formatCurrency(value) {
    return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(value || 0));
  }

  function formatDate(value) {
    const raw = String(value || "").trim();
    const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) {
      return raw || "-";
    }
    const [, year, month, day] = match;
    return `${day}-${month}-${year}`;
  }

  function normalizeDateInput(value) {
    const raw = String(value || "").trim();
    const isoMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (isoMatch) {
      return raw;
    }
    const brMatch = raw.match(/^(\d{2})-(\d{2})-(\d{4})$/);
    if (brMatch) {
      const [, day, month, year] = brMatch;
      return `${year}-${month}-${day}`;
    }
    return null;
  }

  function formatFileSize(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) {
      return `${value} B`;
    }
    if (value < 1024 * 1024) {
      return `${(value / 1024).toFixed(1)} KB`;
    }
    return `${(value / (1024 * 1024)).toFixed(2)} MB`;
  }

  function escapeAttr(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function isPdfFile(file) {
    if (!file) {
      return false;
    }
    const name = String(file.name || "").toLowerCase();
    const type = String(file.type || "").toLowerCase();
    return name.endsWith(".pdf") || type === "application/pdf";
  }

  function isQuotaLocked() {
    return document.body.classList.contains("quota-locked");
  }

  function buildApiError(status, detail) {
    const isDetailObject = detail && typeof detail === "object" && !Array.isArray(detail);
    const message = isDetailObject
      ? String(detail.message || detail.detail || "Falha ao converter arquivo.")
      : String(detail || "Falha ao converter arquivo.");
    const error = new Error(message);
    error.status = Number(status || 0);
    error.detail = detail;
    error.code = isDetailObject && typeof detail.code === "string" ? detail.code : null;
    return error;
  }

  function formatResetAt(resetAtRaw) {
    const parsed = new Date(String(resetAtRaw || ""));
    if (Number.isNaN(parsed.getTime())) {
      return null;
    }
    return new Intl.DateTimeFormat("pt-BR", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
  }

  function showQuotaLockOverlay(detail) {
    if (!quotaLockOverlay) {
      return;
    }
    const resetAt = detail && typeof detail === "object" ? formatResetAt(detail.reset_at) : null;
    if (quotaLockMessage) {
      quotaLockMessage.textContent = resetAt
        ? `Voce usou as 3 conversoes gratuitas desta semana. O proximo ciclo libera novas conversoes em ${resetAt}. Cadastre-se para liberar +10 conversoes semanais agora.`
        : "Voce usou as 3 conversoes gratuitas desta semana. Cadastre-se para liberar +10 conversoes semanais agora.";
    }
    if (quotaLockSignupLink) {
      quotaLockSignupLink.setAttribute("href", QUOTA_SIGNUP_URL);
    }
    syncQuotaAuthLinks();
    document.body.classList.add("quota-locked");
    quotaLockOverlay.classList.remove("hidden");
    quotaLockOverlay.classList.add("is-open");
    convertBtn.disabled = true;
  }

  function hideQuotaLockOverlay() {
    if (!quotaLockOverlay) {
      return;
    }
    quotaLockOverlay.classList.remove("is-open");
    quotaLockOverlay.classList.add("hidden");
    document.body.classList.remove("quota-locked");
    setSelectedFileLabel();
  }

  function forceUnlockUi() {
    if (quotaLockOverlay) {
      quotaLockOverlay.classList.remove("is-open");
      quotaLockOverlay.classList.add("hidden");
    }
    document.body.classList.remove("quota-locked");
  }

  async function syncQuotaLockState() {
    if (!isQuotaLocked()) {
      return;
    }
    const sessionState = await getSessionValidationState();
    if (sessionState === "invalid") {
      hideQuotaLockOverlay();
      clearUserToken();
      syncHeroAuthLinks();
      setStatus("Sua sessão expirou. Faça login novamente para continuar.", "error");
      return;
    }
    if (sessionState === "valid" || sessionState === "unknown") {
      hideQuotaLockOverlay();
      syncHeroAuthLinks();
      setStatus("Conta detectada. Você já pode converter.", "success");
    }
  }

  function setStatus(message, kind) {
    statusMsg.textContent = message || "";
    statusMsg.classList.remove("error", "success");
    if (kind) {
      statusMsg.classList.add(kind);
    }
  }

  function setStatusHtml(html, kind) {
    statusMsg.innerHTML = html || "";
    statusMsg.classList.remove("error", "success");
    if (kind) {
      statusMsg.classList.add(kind);
    }
  }

  function isUnrecognizedPdfLayoutError(message) {
    const normalized = String(message || "").toLowerCase();
    return (
      normalized.includes("pdf text was extracted, but no recognizable transaction row pattern was found") ||
      normalized.includes("pdf text was extracted, but transactions are in an unsupported table layout")
    );
  }

  function syncHeroAuthLinks(profileEmail) {
    const fallbackEmail = String(profileEmail || "").trim() || getProfileHint();
    const hasSession = Boolean(String(profileEmail || "").trim() || getUserToken());
    if (topAuthLoginLink) topAuthLoginLink.classList.toggle("hidden", hasSession);
    if (topAuthPrimaryLink) {
      if (hasSession) {
        const email = fallbackEmail;
        const initial = email.charAt(0).toUpperCase();
        topAuthPrimaryLink.innerHTML = `<span class="top-account-avatar">${initial}</span><span class="top-account-email">${email}</span><span class="top-account-caret">&#9662;</span>`;
        topAuthPrimaryLink.classList.add("top-account-trigger");
      } else {
        topAuthPrimaryLink.textContent = "Converter agora";
        topAuthPrimaryLink.classList.remove("top-account-trigger");
      }
      topAuthPrimaryLink.setAttribute("href", hasSession ? "./client-area.html" : "./ofx-convert.html");
    }
  }

  async function hydrateTopAccountEmail() {
    const token = getUserToken();
    if (!topAuthPrimaryLink) return;
    try {
      const requestInit = {
        credentials: "include",
      };
      if (token) {
        requestInit.headers = { authorization: `Bearer ${token}` };
      }
      const response = await fetch(`${apiBase}/auth/me`, {
        ...requestInit,
      });
      if (!response.ok) {
        if (response.status === 401 && token) clearUserToken();
        return;
      }
      const payload = await response.json().catch(() => ({}));
      const email = String(payload.email || "conta").trim() || "conta";
      setProfileHint(email);
      syncHeroAuthLinks(email);
    } catch (_error) {
      // Keep fallback.
    }
  }

  function markChangedRow(rowId, kind) {
    state.lastChangedRowId = rowId || null;
    state.lastChangedRowKind = rowId ? (kind || "changed") : null;
    if (state.rowHighlightTimer) {
      window.clearTimeout(state.rowHighlightTimer);
      state.rowHighlightTimer = null;
    }
    if (!rowId) {
      return;
    }
    state.rowHighlightTimer = window.setTimeout(() => {
      state.lastChangedRowId = null;
      state.lastChangedRowKind = null;
      state.rowHighlightTimer = null;
      renderRows();
    }, 1800);
  }

  function saveViewState(payload) {
    try {
      localStorage.setItem(VIEW_STATE_KEY, JSON.stringify(payload));
    } catch (_error) {
      // Ignore storage failures.
    }
  }

  function loadViewState() {
    try {
      const raw = localStorage.getItem(VIEW_STATE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") {
        return null;
      }
      return parsed;
    } catch (_error) {
      return null;
    }
  }

  function clearViewState() {
    try {
      localStorage.removeItem(VIEW_STATE_KEY);
    } catch (_error) {
      // Ignore storage failures.
    }
  }

  function getNavigationType() {
    const entries = window.performance && typeof window.performance.getEntriesByType === "function"
      ? window.performance.getEntriesByType("navigation")
      : [];
    const navigationEntry = entries && entries.length > 0 ? entries[0] : null;
    if (navigationEntry && typeof navigationEntry.type === "string") {
      return navigationEntry.type;
    }
    if (window.performance && window.performance.navigation) {
      const legacyType = window.performance.navigation.type;
      if (legacyType === 1) return "reload";
      if (legacyType === 2) return "back_forward";
      return "navigate";
    }
    return "navigate";
  }

  function getCurrentFileMeta() {
    const file = input.files && input.files[0];
    if (file) {
      return {
        file_name: file.name || null,
        file_size: Number(file.size || 0),
      };
    }
    if (state.restoredFileMeta && state.restoredFileMeta.name) {
      return {
        file_name: state.restoredFileMeta.name,
        file_size: Number(state.restoredFileMeta.size || 0),
      };
    }
    return {
      file_name: null,
      file_size: null,
    };
  }

  function persistCurrentViewState() {
    if (!state.analysisId || !state.processingId || !state.analysisSnapshot) {
      return;
    }
    const previewRowsNoRowId = state.previewRows.map(({ rowId, ...row }) => row);
    const originalRowsNoRowId = state.originalRows.map(({ rowId, ...row }) => row);
    const { file_name, file_size } = getCurrentFileMeta();
    saveViewState({
      processing_id: state.processingId,
      analysis_id: state.analysisId,
      analysis: {
        ...state.analysisSnapshot,
        preview_transactions: previewRowsNoRowId,
      },
      quota_text: quotaRemainingNode.textContent || "-",
      file_name,
      file_size,
      preview_rows: previewRowsNoRowId,
      original_rows: originalRowsNoRowId,
      editing_row_id: state.editingRowId,
      edit_draft: state.editDraft ? { ...state.editDraft } : null,
      updated_at: state.analysisSnapshot.updated_at || null,
    });
  }

  function setLoading(isLoading) {
    state.isLoading = isLoading;
    convertBtn.disabled = isQuotaLocked() || isLoading || !input.files || input.files.length === 0;
    convertBtn.textContent = isLoading ? "Convertendo..." : "Converter";
  }

  function setSelectedFileLabel() {
    const file = input.files && input.files[0];
    const restoredMeta = state.restoredFileMeta;
    const hasRestoredMeta = !file && restoredMeta && restoredMeta.name;
    selectedFile.textContent = file
      ? `${file.name} (${formatFileSize(file.size)})`
      : hasRestoredMeta
        ? `${restoredMeta.name} (${formatFileSize(restoredMeta.size)})`
        : "Nenhum arquivo selecionado";
    convertBtn.disabled = isQuotaLocked() || !file || state.isLoading;
    if (dropzoneEmpty && dropzoneLoaded && dropzoneFileMeta) {
      if (file) {
        state.restoredFileMeta = null;
        dropzone.classList.add("is-filled");
        dropzoneEmpty.classList.add("hidden");
        dropzoneLoaded.classList.remove("hidden");
        dropzoneFileMeta.textContent = `${file.name} - ${formatFileSize(file.size)}`;
      } else if (hasRestoredMeta) {
        dropzone.classList.add("is-filled");
        dropzoneEmpty.classList.add("hidden");
        dropzoneLoaded.classList.remove("hidden");
        dropzoneFileMeta.textContent = `${restoredMeta.name} - ${formatFileSize(restoredMeta.size)}`;
      } else {
        dropzone.classList.remove("is-filled");
        dropzoneEmpty.classList.remove("hidden");
        dropzoneLoaded.classList.add("hidden");
        dropzoneFileMeta.textContent = "Pronto para conversão";
      }
    }
  }

  function resetConversionSession(options) {
    const silent = Boolean(options && options.silent);
    input.value = "";
    if (state.rowHighlightTimer) {
      window.clearTimeout(state.rowHighlightTimer);
      state.rowHighlightTimer = null;
    }
    state.analysisId = null;
    state.processingId = null;
    state.restoredFileMeta = null;
    state.previewRows = [];
    state.originalRows = [];
    state.editingRowId = null;
    state.editDraft = null;
    state.analysisSnapshot = null;
    state.lastChangedRowId = null;
    state.lastChangedRowKind = null;
    markChangedRow(null);
    if (addRowBtn) addRowBtn.disabled = true;
    if (downloadOfxBtn) downloadOfxBtn.disabled = true;
    reviewRows.innerHTML = "";
    kpis.innerHTML = "";
    reviewSection.classList.add("hidden");
    downloadSection.classList.add("hidden");
    if (analysisIdNode) analysisIdNode.textContent = "-";
    if (processingIdNode) processingIdNode.textContent = "-";
    quotaRemainingNode.textContent = "-";
    setLoading(false);
    setSelectedFileLabel();
    clearViewState();
    if (silent) {
      setStatus("", null);
      return;
    }
    setStatus("Arquivo removido. Selecione outro PDF para continuar.", null);
  }

  function clearSelectedFile() {
    resetConversionSession({ silent: false });
  }

  function resolveConvertedPages(analysis) {
    const metrics = analysis && typeof analysis === "object" ? analysis.pdf_processing_metrics : null;
    const pageCount = metrics && typeof metrics === "object" ? Number(metrics.page_count) : NaN;
    if (Number.isFinite(pageCount) && pageCount > 0) {
      return String(Math.trunc(pageCount));
    }
    return "1";
  }

  function renderKpis(analysis) {
    const pagesConverted = resolveConvertedPages(analysis);
    const entries = [
      ["Transações", analysis.transactions_total],
      ["Páginas convertidas", pagesConverted],
      ["Entradas", formatCurrency(analysis.total_inflows)],
      ["Saídas", formatCurrency(analysis.total_outflows)],
      ["Saldo", formatCurrency(analysis.net_total)],
    ];

    kpis.innerHTML = entries
      .map(([label, value]) => `
        <article class="kpi">
          <p class="kpi-label">${label}</p>
          <p class="kpi-value">${value}</p>
        </article>
      `)
      .join("");
  }

  function toPositiveMoneyString(value) {
    const numeric = Math.abs(Number(value || 0));
    if (!Number.isFinite(numeric) || numeric === 0) {
      return "";
    }
    return numeric.toFixed(2);
  }

  function parseMoneyInput(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return null;
    }
    let normalized = raw.replace(/\s+/g, "");
    if (normalized.includes(",") && normalized.includes(".")) {
      normalized = normalized.replace(/\./g, "").replace(",", ".");
    } else if (normalized.includes(",")) {
      normalized = normalized.replace(",", ".");
    }
    const parsed = Number(normalized);
    if (!Number.isFinite(parsed) || parsed < 0) {
      return null;
    }
    return parsed;
  }

  function getCreditAmount(row) {
    const amount = Number(row.amount || 0);
    return amount > 0 ? amount : null;
  }

  function getDebitAmount(row) {
    const amount = Number(row.amount || 0);
    return amount < 0 ? Math.abs(amount) : null;
  }

  function setPreviewRows(rows) {
    state.previewRows = (rows || []).map((row, idx) => ({
      ...row,
      rowId: row.rowId || `row_${idx + 1}`,
    }));
  }

  function setOriginalRows(rows) {
    state.originalRows = (rows || []).map((row, idx) => ({
      ...row,
      rowId: row.rowId || `row_${idx + 1}`,
    }));
  }

  function buildPatchFromHistoryRow(rowId, row, action) {
    const amount = Number(row.amount || 0);
    return {
      row_id: rowId,
      action: action || "update",
      date: String(row.date || ""),
      description: String(row.description || ""),
      credit: amount > 0 ? Number(amount.toFixed(2)) : null,
      debit: amount < 0 ? Number(Math.abs(amount).toFixed(2)) : null,
    };
  }

  function getOriginalRow(rowId) {
    return state.originalRows.find((item) => item.rowId === rowId) || null;
  }

  function isRowChanged(row) {
    const original = getOriginalRow(row.rowId);
    if (!original) {
      return false;
    }
    return (
      String(original.date || "") !== String(row.date || "") ||
      String(original.description || "") !== String(row.description || "") ||
      Number(original.amount || 0) !== Number(row.amount || 0) ||
      Boolean(original.is_deleted) !== Boolean(row.is_deleted)
    );
  }

  async function revertRowToOriginal(rowId) {
    const original = getOriginalRow(rowId);
    if (!original) {
      setStatus("Não há versão original para esta linha.", "error");
      return;
    }
    if (!state.processingId || !state.analysisSnapshot) {
      setStatus("Converta um arquivo antes de voltar alterações.", "error");
      return;
    }
    try {
      setStatus("Voltando para versão original...", null);
      const payload = await postConvertEdit(state.processingId, buildPatchFromHistoryRow(rowId, original, "restore"));
      setPreviewRows(payload.preview_transactions || []);
      state.analysisSnapshot.preview_transactions = state.previewRows.map(({ rowId: _rowId, ...row }) => row);
      state.analysisSnapshot.transactions_total = Number(payload.transactions_total || state.analysisSnapshot.transactions_total || 0);
      state.analysisSnapshot.total_inflows = Number(payload.total_inflows || state.analysisSnapshot.total_inflows || 0);
      state.analysisSnapshot.total_outflows = Number(payload.total_outflows || state.analysisSnapshot.total_outflows || 0);
      state.analysisSnapshot.net_total = Number(payload.net_total || state.analysisSnapshot.net_total || 0);
      state.analysisSnapshot.updated_at = payload.updated_at || state.analysisSnapshot.updated_at || null;
      renderKpis(state.analysisSnapshot);
      markChangedRow(rowId);
      renderRows();
      persistCurrentViewState();
      setStatus("Linha voltou ao valor original.", "success");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Falha ao voltar linha.", "error");
    }
  }

  async function deleteRow(rowId) {
    if (!state.processingId || !state.analysisSnapshot) {
      setStatus("Converta um arquivo antes de apagar linhas.", "error");
      return;
    }
    const row = state.previewRows.find((item) => item.rowId === rowId);
    if (!row) {
      setStatus("Linha não encontrada para exclusão.", "error");
      return;
    }
    try {
      const payload = await postConvertEdit(state.processingId, {
        row_id: rowId,
        action: "delete",
      });
      setPreviewRows(payload.preview_transactions || []);
      state.analysisSnapshot.preview_transactions = state.previewRows.map(({ rowId: _rowId, ...item }) => item);
      state.analysisSnapshot.transactions_total = Number(payload.transactions_total || state.analysisSnapshot.transactions_total || 0);
      state.analysisSnapshot.total_inflows = Number(payload.total_inflows || state.analysisSnapshot.total_inflows || 0);
      state.analysisSnapshot.total_outflows = Number(payload.total_outflows || state.analysisSnapshot.total_outflows || 0);
      state.analysisSnapshot.net_total = Number(payload.net_total || state.analysisSnapshot.net_total || 0);
      state.analysisSnapshot.updated_at = payload.updated_at || state.analysisSnapshot.updated_at || null;
      renderKpis(state.analysisSnapshot);
      markChangedRow(rowId);
      renderRows();
      persistCurrentViewState();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Falha ao apagar linha.", "error");
    }
  }

  function startInsertRow() {
    if (!state.processingId) {
      setStatus("Converta um arquivo antes de adicionar linhas.", "error");
      return;
    }
    if (state.editingRowId) {
      setStatus("Salve ou cancele a edição atual antes de criar nova linha.", "error");
      return;
    }
    const draftId = `row_draft_${Date.now()}`;
    state.previewRows.unshift({
      rowId: draftId,
      date: "",
      description: "",
      amount: 0,
      category: "Outros",
      reconciliation_status: "unmatched",
      is_deleted: false,
    });
    state.editingRowId = draftId;
    state.editDraft = {
      date: "",
      description: "",
      credit: "",
      debit: "",
    };
    markChangedRow(draftId, "new");
    renderRows();
    persistCurrentViewState();
  }

  function startEditingRow(rowId) {
    const row = state.previewRows.find((item) => item.rowId === rowId);
    if (!row) {
      return;
    }
    state.editingRowId = rowId;
    state.editDraft = {
      date: formatDate(row.date),
      description: row.description || "",
      credit: toPositiveMoneyString(getCreditAmount(row)),
      debit: toPositiveMoneyString(getDebitAmount(row)),
    };
    renderRows();
  }

  function cancelEditingRow() {
    if (isDraftRowId(state.editingRowId)) {
      state.previewRows = state.previewRows.filter((row) => row.rowId !== state.editingRowId);
    }
    state.editingRowId = null;
    state.editDraft = null;
    renderRows();
    persistCurrentViewState();
  }

  function updateEditDraft(field, value) {
    if (!state.editDraft) {
      return;
    }
    state.editDraft[field] = value;
    persistCurrentViewState();
  }

  async function saveEditingRow(rowId) {
    if (!state.editDraft) {
      return;
    }
    const normalizedDate = normalizeDateInput(state.editDraft.date);
    if (!normalizedDate) {
      setStatus("Data inválida. Use dd-mm-yyyy.", "error");
      return;
    }

    const description = String(state.editDraft.description || "").trim();
    if (!description) {
      setStatus("Histórico é obrigatório.", "error");
      return;
    }

    const credit = parseMoneyInput(state.editDraft.credit);
    const debit = parseMoneyInput(state.editDraft.debit);

    if ((credit === null && debit === null) || (credit !== null && debit !== null)) {
      setStatus("Preencha apenas crédito ou débito.", "error");
      return;
    }

    if (!state.processingId) {
      setStatus("Converta um arquivo antes de editar.", "error");
      return;
    }

    const rowBeforeSave = state.previewRows.find((item) => item.rowId === rowId);
    if (!rowBeforeSave) {
      setStatus("Linha não encontrada para edição.", "error");
      return;
    }

    try {
      setStatus("Salvando edição...", null);
      const isDraft = isDraftRowId(rowId);
      const payload = await postConvertEdit(
        state.processingId,
        isDraft
          ? {
              action: "insert",
              insert_position: 0,
              date: normalizedDate,
              description,
              credit,
              debit,
            }
          : {
              row_id: rowId,
              date: normalizedDate,
              description,
              credit,
              debit,
            },
      );

      state.editingRowId = null;
      state.editDraft = null;
      setPreviewRows(payload.preview_transactions || []);
      if (isDraft) {
        setOriginalRows(payload.preview_transactions || []);
      }
      if (state.analysisSnapshot) {
        state.analysisSnapshot.preview_transactions = state.previewRows.map(({ rowId: _rowId, ...row }) => row);
        state.analysisSnapshot.transactions_total = Number(payload.transactions_total || state.analysisSnapshot.transactions_total || 0);
        state.analysisSnapshot.total_inflows = Number(payload.total_inflows || state.analysisSnapshot.total_inflows || 0);
        state.analysisSnapshot.total_outflows = Number(payload.total_outflows || state.analysisSnapshot.total_outflows || 0);
        state.analysisSnapshot.net_total = Number(payload.net_total || state.analysisSnapshot.net_total || 0);
        state.analysisSnapshot.updated_at = payload.updated_at || state.analysisSnapshot.updated_at || null;
        renderKpis(state.analysisSnapshot);
      }
      markChangedRow(isDraft ? "row_1" : rowId, isDraft ? "new" : "changed");
      renderRows();
      persistCurrentViewState();
      setStatus("Linha atualizada na prévia.", "success");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Falha ao salvar edição.", "error");
    }
  }

  function renderRows() {
    const rows = state.previewRows;
    if (!rows || rows.length === 0) {
      reviewRows.innerHTML = '<tr><td colspan="5">Nenhuma transação para exibir.</td></tr>';
      return;
    }

    reviewRows.innerHTML = rows
      .map((row) => {
        const rowClass =
          row.rowId === state.lastChangedRowId
            ? state.lastChangedRowKind === "new"
              ? "row-new"
              : "row-changed"
            : "";
        const rowDeleted = Boolean(row.is_deleted);
        const isEditing = row.rowId === state.editingRowId && state.editDraft;
        if (isEditing) {
          return `
          <tr class="${rowClass}">
            <td><input class="cell-input cell-input-date" data-edit-field="date" value="${escapeAttr(state.editDraft.date)}" /></td>
            <td><input class="cell-input cell-input-description" data-edit-field="description" value="${escapeAttr(state.editDraft.description)}" /></td>
            <td><input class="cell-input cell-input-money" data-edit-field="credit" inputmode="decimal" placeholder="0,00" value="${escapeAttr(state.editDraft.credit)}" /></td>
            <td><input class="cell-input cell-input-money" data-edit-field="debit" inputmode="decimal" placeholder="0,00" value="${escapeAttr(state.editDraft.debit)}" /></td>
            <td class="actions-cell">
              <button class="btn btn-secondary btn-inline" type="button" data-action="save-row" data-row-id="${row.rowId}" aria-label="Salvar edição">
                <span class="btn-icon" aria-hidden="true">&#10003;</span><span>Salvar</span>
              </button>
              <button class="btn btn-inline btn-ghost" type="button" data-action="cancel-row" aria-label="Cancelar edição">
                <span class="btn-icon" aria-hidden="true">&#10005;</span><span>Cancelar</span>
              </button>
            </td>
          </tr>
        `;
        }
        const rowChanged = isRowChanged(row);
        const creditAmount = getCreditAmount(row);
        const debitAmount = getDebitAmount(row);
        const creditMarkup = creditAmount !== null
          ? `<span class="amount-credit">${formatCurrency(creditAmount)}</span>`
          : '<span class="amount-empty">—</span>';
        const debitMarkup = debitAmount !== null
          ? `<span class="amount-debit">${formatCurrency(debitAmount)}</span>`
          : '<span class="amount-empty">—</span>';
        return `
          <tr class="${rowClass} ${rowDeleted ? "row-deleted" : ""}">
            <td>${formatDate(row.date)}</td>
            <td>${row.description || "-"}</td>
            <td>${creditMarkup}</td>
            <td>${debitMarkup}</td>
            <td class="actions-cell">
              ${
                !rowDeleted && !isDraftRowId(row.rowId)
                  ? `<button class="btn btn-inline btn-secondary" type="button" data-action="edit-row" data-row-id="${row.rowId}" aria-label="Editar linha">
                <span class="btn-icon" aria-hidden="true">&#9998;</span><span>Editar</span>
              </button>
              <button class="btn btn-inline btn-ghost" type="button" data-action="delete-row" data-row-id="${row.rowId}" aria-label="Apagar linha">
                <span class="btn-icon" aria-hidden="true">&#128465;</span><span>Apagar</span>
              </button>`
                  : ""
              }
              ${
                rowChanged
                  ? `<button class="btn btn-inline btn-ghost" type="button" data-action="revert-row" data-row-id="${row.rowId}" aria-label="Voltar para valor original">
                <span class="btn-icon" aria-hidden="true">&#8634;</span><span>Voltar</span>
              </button>`
                  : ""
              }
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function restoreViewFromState(viewState) {
    const analysis = viewState.analysis;
    if (!analysis || !analysis.analysis_id) {
      return;
    }

    state.analysisId = viewState.analysis_id || analysis.analysis_id;
    state.processingId = viewState.processing_id || analysis.analysis_id;
    state.analysisSnapshot = { ...analysis };
    if (viewState.updated_at && !state.analysisSnapshot.updated_at) {
      state.analysisSnapshot.updated_at = viewState.updated_at;
    }
    state.restoredFileMeta = {
      name: String(viewState.file_name || "").trim() || "arquivo_restaurado.pdf",
      size: Number(viewState.file_size || 0),
    };

    const restoredRows = Array.isArray(viewState.preview_rows)
      ? viewState.preview_rows
      : analysis.preview_transactions || [];
    const restoredOriginalRows = Array.isArray(viewState.original_rows)
      ? viewState.original_rows
      : analysis.preview_transactions || [];
    renderKpis(analysis);
    setPreviewRows(restoredRows);
    setOriginalRows(restoredOriginalRows);
    markChangedRow(null);
    if (viewState.editing_row_id && viewState.edit_draft && state.previewRows.some((row) => row.rowId === viewState.editing_row_id)) {
      state.editingRowId = viewState.editing_row_id;
      state.editDraft = { ...viewState.edit_draft };
    } else {
      state.editingRowId = null;
      state.editDraft = null;
    }
    renderRows();
    setSelectedFileLabel();

    if (analysisIdNode) analysisIdNode.textContent = state.analysisId || "-";
    if (processingIdNode) processingIdNode.textContent = state.processingId || "-";
    quotaRemainingNode.textContent = viewState.quota_text || "-";

    reviewSection.classList.remove("hidden");
    downloadSection.classList.remove("hidden");
    if (addRowBtn) addRowBtn.disabled = false;

    const canDownload = Boolean(state.analysisId || state.processingId);
    if (downloadOfxBtn) downloadOfxBtn.disabled = !canDownload;

    setStatus("Sessão restaurada. Você pode continuar o download.", "success");
  }

  async function postConvert(formData) {
    const token = getUserToken();
    const headers = buildOptionalAuthHeaders(token);
    const response = await fetch(`${apiBase}/convert`, {
      method: "POST",
      credentials: "include",
      ...(headers ? { headers } : {}),
      body: formData,
    });

    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw buildApiError(response.status, payload.detail || "Falha ao converter arquivo.");
    }

    return payload;
  }

  function setUploadLimitsText(maxUploadBytes, maxPagesPerFile) {
    if (!uploadLimitsText) return;
    const mb = Number(maxUploadBytes || 0) / (1024 * 1024);
    const pages = Number(maxPagesPerFile || 0);
    const safeMb = Number.isFinite(mb) && mb > 0 ? mb.toFixed(0) : "2";
    const safePages = Number.isFinite(pages) && pages > 0 ? String(pages) : "5";
    uploadLimitsText.textContent = `Somente PDF (ate ${safeMb} MB e ${safePages} paginas por arquivo)`;
  }

  async function syncUploadLimitsBySession() {
    const token = getUserToken();
    try {
      const requestInit = {
        credentials: "include",
      };
      if (token) {
        requestInit.headers = { authorization: `Bearer ${token}` };
      }
      const response = await fetch(`${apiBase}/auth/me`, {
        ...requestInit,
      });
      if (!response.ok) {
        setUploadLimitsText(2 * 1024 * 1024, 5);
        return;
      }
      const me = await response.json().catch(() => ({}));
      setUploadLimitsText(Number(me.max_upload_size_bytes || 2 * 1024 * 1024), Number(me.max_pages_per_file || 5));
    } catch (_error) {
      setUploadLimitsText(2 * 1024 * 1024, 5);
    }
  }

  async function postConvertEdit(processingId, editPatch) {
    const query = buildIdentityQueryParams().toString();
    const token = getUserToken();
    const optionalHeaders = buildOptionalAuthHeaders(token);
    const response = await fetch(`${apiBase}/convert-edits/${processingId}?${query}`, {
      method: "POST",
      credentials: "include",
      headers: {
        ...(optionalHeaders || {}),
        "content-type": "application/json",
      },
      body: JSON.stringify({
        edits: [editPatch],
        expected_updated_at: state.analysisSnapshot ? state.analysisSnapshot.updated_at || null : null,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw buildApiError(response.status, payload.detail || "Falha ao salvar edição.");
    }
    return payload;
  }

  async function runConvert() {
    if (isQuotaLocked()) {
      setStatus("Limite semanal atingido. Crie sua conta para continuar.", "error");
      return;
    }
    const file = input.files && input.files[0];
    if (!file) {
      setStatus("Selecione um arquivo antes de converter.", "error");
      return;
    }
    if (!isPdfFile(file)) {
      setStatus("Este conversor aceita somente arquivos PDF.", "error");
      return;
    }

    setLoading(true);
    setStatus("Processando arquivo...", null);

    try {
      const formData = new FormData();
      formData.append("file", file);
      const token = getUserToken();
      if (token) {
        const sessionState = await getSessionValidationState();
        if (sessionState === "invalid") {
          hideQuotaLockOverlay();
          clearUserToken();
          syncHeroAuthLinks();
          setStatus("Sua sessão expirou. Faça login novamente para continuar.", "error");
          return;
        }
      } else {
        formData.append("anonymous_fingerprint", getAnonymousFingerprint());
      }

      const payload = await postConvert(formData);
      state.quotaMode = String(payload.quota_mode || "conversion").toLowerCase();

      const analysis = payload.analysis;
      state.analysisId = analysis.analysis_id;
      state.processingId = payload.processing_id || analysis.analysis_id;
      state.analysisSnapshot = { ...analysis };
      markChangedRow(null);
      if (addRowBtn) addRowBtn.disabled = false;

      renderKpis(analysis);
      setPreviewRows(analysis.preview_transactions || []);
      setOriginalRows(analysis.preview_transactions || []);
      renderRows();

      if (analysisIdNode) analysisIdNode.textContent = analysis.analysis_id || "-";
      if (processingIdNode) processingIdNode.textContent = state.processingId || "-";
      const quotaLabel = state.quotaMode === "pages" ? "paginas" : "conversoes";
      quotaRemainingNode.textContent = `${payload.quota_remaining} / ${payload.quota_limit} (${quotaLabel})`;

      reviewSection.classList.remove("hidden");
      downloadSection.classList.remove("hidden");
      const canDownload = Boolean(state.analysisId);
      if (downloadOfxBtn) downloadOfxBtn.disabled = !canDownload;

      persistCurrentViewState();

      setStatus("Conversão concluída. Revise os dados e baixe o relatório.", "success");
      reviewSection.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Erro inesperado.";
      const detail = error && typeof error === "object" ? error.detail : null;
      const status = error && typeof error === "object" ? Number(error.status || 0) : 0;
      const code = error && typeof error === "object" ? String(error.code || "") : "";
      if (isUnrecognizedPdfLayoutError(message)) {
        setStatusHtml(
          'Não conseguimos identificar as transações neste PDF. <a href="./contato.html">Falar com suporte</a> ou tente outro arquivo.',
          "error",
        );
        return;
      }
      if (status === 429 && code === "weekly_quota_exceeded") {
        if (!getUserToken()) {
          showQuotaLockOverlay(detail);
          setStatus("Você atingiu o limite gratuito desta semana.", "error");
          return;
        }
      }
      if (status === 429 && code === "monthly_pages_quota_exceeded") {
        setStatus("Voce atingiu o limite mensal de paginas do seu plano.", "error");
        return;
      }
      const normalizedMessage = String(message || "").toLowerCase();
      if (
        status === 400 &&
        (normalizedMessage.includes("invalid identity context") || normalizedMessage.includes("invalid user token"))
      ) {
        hideQuotaLockOverlay();
        clearUserToken();
        syncHeroAuthLinks();
        setStatus("Sua sessão expirou. Faça login novamente para continuar.", "error");
        return;
      }
      setStatus(message, "error");
    } finally {
      setLoading(false);
    }
  }

  async function runDownloadOfx() {
    if (!state.processingId) {
      setStatus("Converta um arquivo antes de baixar.", "error");
      return;
    }
    const query = buildIdentityQueryParams();
    query.set("format", "ofx");
    const token = getUserToken();
    const headers = buildOptionalAuthHeaders(token);

    try {
      setStatus("Preparando download...", null);
      const response = await fetch(`${apiBase}/convert-report/${state.processingId}?${query.toString()}`, {
        method: "GET",
        credentials: "include",
        ...(headers ? { headers } : {}),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = payload && typeof payload === "object" ? payload.detail : null;
        const message = String(detail || "Falha ao baixar OFX.");
        setStatus(message, "error");
        return;
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `ofxsimples-${state.processingId}.ofx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
      setStatus("Download iniciado.", "success");
    } catch (_error) {
      setStatus("Falha de rede ao baixar OFX.", "error");
    }
  }

  function bindDropzone() {
    dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragover");
    });

    dropzone.addEventListener("dragleave", () => {
      dropzone.classList.remove("is-dragover");
    });

    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragover");
      if (!event.dataTransfer || !event.dataTransfer.files || event.dataTransfer.files.length === 0) {
        return;
      }
      if (!isPdfFile(event.dataTransfer.files[0])) {
        setStatus("Este conversor aceita somente arquivos PDF.", "error");
        return;
      }
      const transfer = new DataTransfer();
      transfer.items.add(event.dataTransfer.files[0]);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
  }

  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (file && !isPdfFile(file)) {
      input.value = "";
      setSelectedFileLabel();
      setStatus("Este conversor aceita somente arquivos PDF.", "error");
      return;
    }
    setSelectedFileLabel();
    setStatus("", null);
  });

  reviewRows.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const actionTarget = target.closest("[data-action]");
    if (!(actionTarget instanceof HTMLElement)) {
      return;
    }
    const action = actionTarget.dataset.action;
    if (!action) {
      return;
    }
    if (action === "edit-row") {
      const rowId = actionTarget.dataset.rowId || "";
      const row = state.previewRows.find((item) => item.rowId === rowId);
      if (row && row.is_deleted) {
        return;
      }
      startEditingRow(rowId);
      return;
    }
    if (action === "cancel-row") {
      cancelEditingRow();
      return;
    }
    if (action === "save-row") {
      void saveEditingRow(actionTarget.dataset.rowId || "");
      return;
    }
    if (action === "delete-row") {
      void deleteRow(actionTarget.dataset.rowId || "");
      return;
    }
    if (action === "revert-row") {
      void revertRowToOriginal(actionTarget.dataset.rowId || "");
    }
  });

  reviewRows.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    const field = target.dataset.editField;
    if (!field || !state.editDraft) {
      return;
    }
    updateEditDraft(field, target.value);
  });
  convertBtn.addEventListener("click", runConvert);
  if (addRowBtn) addRowBtn.addEventListener("click", startInsertRow);
  if (downloadOfxBtn) downloadOfxBtn.addEventListener("click", runDownloadOfx);
  if (clearFileBtn) {
    clearFileBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      clearSelectedFile();
    });
  }

  bindDropzone();
  window.addEventListener("focus", () => {
    void syncQuotaLockState();
  });
  window.addEventListener("storage", () => {
    void syncQuotaLockState();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void syncQuotaLockState();
    }
  });
  if (menuToggle && topLinks) {
    menuToggle.addEventListener("click", function () {
      const open = topLinks.classList.toggle("is-open");
      menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  forceUnlockUi();
  setSelectedFileLabel();
  const didForceLogout = consumeLogoutQueryFlag();
  syncHeroAuthLinks();
  void hydrateTopAccountEmail();
  void syncUploadLimitsBySession();
  syncQuotaAuthLinks();
  const navigationType = getNavigationType();
  const shouldRestoreState = navigationType === "reload";
  if (!shouldRestoreState) {
    clearViewState();
  }
  const persistedState = loadViewState();
  if (persistedState) {
    restoreViewFromState(persistedState);
  }
  void syncQuotaLockState();
  if (didForceLogout) {
    setStatus("Sessão encerrada. Você está no modo gratuito (anônimo).", "success");
  }
})();
