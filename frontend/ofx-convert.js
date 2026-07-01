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
  const processingProgress = document.getElementById("processing-progress");
  const processingProgressFill = document.getElementById("processing-progress-fill");
  const topAuthLoginLink = document.getElementById("top-auth-login-link");
  const topAuthPrimaryLink = document.getElementById("top-auth-primary-link");
  const menuToggle = document.getElementById("menu-toggle");
  const topLinks = document.getElementById("top-links");
  const quotaLockOverlay = document.getElementById("quota-lock-overlay");
  const quotaLockBadge = document.getElementById("quota-lock-badge");
  const quotaLockTitle = document.getElementById("quota-lock-title");
  const quotaLockMessage = document.getElementById("quota-lock-message");
  const quotaLockSignupLink = document.getElementById("quota-lock-signup-link");
  const quotaLockLoginLink = document.getElementById("quota-lock-login-link");
  const uploadLimitsText = document.getElementById("upload-limits-text");
  const TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024;
  const TEXT_PDF_MAX_PAGES_PER_FILE = 250;

  const reviewSection = document.getElementById("review-section");
  const downloadSection = document.getElementById("download-section");
  const reviewRows = document.getElementById("review-rows");
  const kpis = document.getElementById("kpis");
  const addRowBtn = document.getElementById("add-row-btn");

  const analysisIdNode = document.getElementById("analysis-id");
  const processingIdNode = document.getElementById("processing-id");
  const quotaRemainingLabelNode = document.getElementById("quota-remaining-label");
  const quotaRemainingNode = document.getElementById("quota-remaining");
  const downloadSingleBtn = document.getElementById("download-report-btn");
  const downloadOfxBtn = document.getElementById("download-ofx-btn");
  const downloadExcelBtn = document.getElementById("download-excel-btn");
  const hasDualDownloadButtons = Boolean(downloadOfxBtn && downloadExcelBtn);
  const defaultDownloadBtn = downloadSingleBtn || downloadOfxBtn;
  const outputFormat = hasDualDownloadButtons
    ? "ofx"
    : String(document.body.getAttribute("data-output-format") || "ofx")
      .trim()
      .toLowerCase() === "excel"
      ? "excel"
      : "ofx";
  const requireAuthAccess = String(document.body.getAttribute("data-require-auth") || "").trim().toLowerCase() === "true";
  const PUBLIC_CONVERT_PATH = "./convert.html";
  const INTERNAL_LOGIN_URL = "./login.html?next=%2Fofx-convert.html&force_auth=1";
  const VIEW_STATE_KEY = `ofxsimples_ofx_convert_view_state_${outputFormat}_v1`;

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
    quotaRemaining: null,
    quotaLimit: null,
    openingBalanceOverride: null,
    openingBalanceManuallyEdited: false,
    closingBalanceOverride: null,
    closingBalanceManuallyEdited: false,
    bankBranchOverride: "",
    accountNumberOverride: "",
    bankCodeOverride: "",
    progressDisplay: 0,
    progressTarget: 0,
    progressFrame: null,
    progressDriftTimer: null,
    statusPendingTimer: null,
    lastStatusAt: 0,
    quotaLockVariant: null,
  };
  let bankCodeOptions = [{ code: "", label: "Selecione o banco", name: "", short_name: "", aliases: [] }];

  function isPagesQuotaMode(mode) {
    return String(mode || "").toLowerCase() === "pages";
  }

  function normalizeQuotaMode(mode) {
    return isPagesQuotaMode(mode) ? "pages" : "conversion";
  }

  function inferQuotaModeFromText(value) {
    return /p[áa]ginas/i.test(String(value || "")) ? "pages" : "conversion";
  }

  function updateQuotaRemainingLabel() {
    if (!quotaRemainingLabelNode) {
      return;
    }
    quotaRemainingLabelNode.textContent = isPagesQuotaMode(state.quotaMode)
      ? "páginas restantes:"
      : "conversões restantes:";
  }

  function updateQuotaRemainingValue(remaining, limit) {
    const parsedRemaining = Number(remaining);
    const parsedLimit = Number(limit);
    const hasNumbers = Number.isFinite(parsedRemaining) && Number.isFinite(parsedLimit);
    const quotaLabel = isPagesQuotaMode(state.quotaMode) ? "páginas" : "conversões";
    if (quotaRemainingNode) {
      quotaRemainingNode.textContent = hasNumbers ? `${parsedRemaining} / ${parsedLimit} (${quotaLabel})` : "-";
    }
    updateQuotaRemainingLabel();
  }

  function parseQuotaNumbersFromText(value) {
    const match = String(value || "").match(/(\d+)\s*\/\s*(\d+)/);
    if (!match) {
      return null;
    }
    const remaining = Number(match[1]);
    const limit = Number(match[2]);
    if (!Number.isFinite(remaining) || !Number.isFinite(limit)) {
      return null;
    }
    return { remaining, limit };
  }

  function setDownloadButtonsDisabled(isDisabled) {
    if (defaultDownloadBtn) {
      defaultDownloadBtn.disabled = isDisabled;
    }
    if (downloadOfxBtn) {
      downloadOfxBtn.disabled = isDisabled;
    }
    if (downloadExcelBtn) {
      downloadExcelBtn.disabled = isDisabled;
    }
  }

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
  const QUOTA_PLANS_URL = "./planos.html?reason=quota";
  const QUOTA_SUPPORT_URL = "./contato.html?reason=quota";
  const USER_TOKEN_KEY = "ofxsimples_user_token";
  const USER_TOKEN_COOKIE = "ofxsimples_user_token";
  const OAUTH_DEBUG_KEY = "ofxsimples_last_google_oauth_debug";
  const TOKEN_SHARED_COOKIE_ALLOWLIST = ["ofxsimples.com.br"];
  const PROFILE_HINT_KEY = "ofxsimples_profile_hint";
  const ANON_FINGERPRINT_KEY = "ofxsimples_anon_fingerprint";
  const QUOTA_LOCK_VARIANT_ANONYMOUS = "anonymous-free-limit";
  const QUOTA_LOCK_VARIANT_REGISTERED = "registered-free-limit";

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

  function persistOAuthDebug(payload) {
    try {
      sessionStorage.setItem(
        OAUTH_DEBUG_KEY,
        JSON.stringify({
          ...payload,
          at: new Date().toISOString(),
        }),
      );
    } catch (_error) {
      // no-op
    }
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

  function setQuotaLockActions(primaryLabel, primaryHref, secondaryLabel, secondaryHref) {
    if (quotaLockSignupLink) {
      quotaLockSignupLink.textContent = primaryLabel || "";
      quotaLockSignupLink.setAttribute("href", primaryHref || QUOTA_SIGNUP_URL);
    }
    if (quotaLockLoginLink) {
      quotaLockLoginLink.textContent = secondaryLabel || "";
      quotaLockLoginLink.setAttribute("href", secondaryHref || QUOTA_LOGIN_URL);
      quotaLockLoginLink.classList.toggle("hidden", !secondaryLabel);
    }
  }

  function openQuotaLockOverlay(variant) {
    state.quotaLockVariant = variant || null;
    if (quotaLockOverlay) {
      if (variant) {
        quotaLockOverlay.dataset.variant = variant;
      } else {
        delete quotaLockOverlay.dataset.variant;
      }
      quotaLockOverlay.classList.remove("hidden");
      quotaLockOverlay.classList.add("is-open");
    }
    document.body.classList.add("quota-locked");
    convertBtn.disabled = true;
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
      persistOAuthDebug({
        stage: "ofx_convert_auth_me_result",
        path: window.location.pathname,
        hasToken: Boolean(token),
        status: response.status,
        ok: response.ok,
      });
      if (response.ok) {
        return "valid";
      }
      if (response.status === 401) {
        return token ? "invalid" : "missing";
      }
      return "unknown";
    } catch (_error) {
      persistOAuthDebug({
        stage: "ofx_convert_auth_me_network_error",
        path: window.location.pathname,
        hasToken: Boolean(token),
      });
      return "unknown";
    }
  }

  function redirectToInternalLogin() {
    window.location.replace(INTERNAL_LOGIN_URL);
  }

  async function enforceAuthenticatedAccess() {
    if (!requireAuthAccess) {
      return true;
    }
    const token = getUserToken();
    if (!token) {
      redirectToInternalLogin();
      return false;
    }
    const sessionState = await getSessionValidationState();
    if (sessionState !== "valid") {
      clearUserToken();
      redirectToInternalLogin();
      return false;
    }
    return true;
  }

  function buildOptionalAuthHeaders(userToken) {
    const token = String(userToken || "").trim();
    if (!token) {
      return null;
    }
    return { authorization: `Bearer ${token}` };
  }

  function parseDownloadFilenameFromContentDisposition(headerValue) {
    const raw = String(headerValue || "").trim();
    if (!raw) {
      return "";
    }
    const utf8Match = raw.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match && utf8Match[1]) {
      try {
        return decodeURIComponent(utf8Match[1].trim());
      } catch (_error) {
        return utf8Match[1].trim();
      }
    }
    const quotedMatch = raw.match(/filename=\"([^\"]+)\"/i);
    if (quotedMatch && quotedMatch[1]) {
      return quotedMatch[1].trim();
    }
    const plainMatch = raw.match(/filename=([^;]+)/i);
    if (plainMatch && plainMatch[1]) {
      return plainMatch[1].trim();
    }
    return "";
  }

  function buildFallbackDownloadFilename(extension) {
    const safeExtension = String(extension || "").trim().toLowerCase();
    const { file_name } = getCurrentFileMeta();
    const rawName = String(file_name || "").trim();
    if (rawName) {
      const stem = rawName.replace(/\.[^/.]+$/, "").trim();
      if (stem) {
        return `${stem}.${safeExtension}`;
      }
    }
    const processingId = state.processingId || state.analysisId;
    if (processingId) {
      return `ofxsimples-${processingId}.${safeExtension}`;
    }
    return `ofxsimples-convert.${safeExtension}`;
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

  function toIsoDateInputValue(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return "";
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
      return raw;
    }
    const brMatch = raw.match(/^(\d{2})-(\d{2})-(\d{4})$/);
    if (brMatch) {
      const [, day, month, year] = brMatch;
      return `${year}-${month}-${day}`;
    }
    return "";
  }

  function applyDateInputMask(value) {
    const digits = String(value || "").replace(/\D/g, "").slice(0, 8);
    if (digits.length <= 2) {
      return digits;
    }
    if (digits.length <= 4) {
      return `${digits.slice(0, 2)}-${digits.slice(2)}`;
    }
    return `${digits.slice(0, 2)}-${digits.slice(2, 4)}-${digits.slice(4)}`;
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

  function resolveSseFailedStatus(code) {
    const normalized = String(code || "").trim().toLowerCase();
    if (normalized === "weekly_quota_exceeded" || normalized === "monthly_pages_quota_exceeded") {
      return 429;
    }
    if (normalized === "file_too_large") {
      return 413;
    }
    return 400;
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
    if (quotaLockBadge) {
      quotaLockBadge.textContent = "Limite semanal atingido";
    }
    if (quotaLockTitle) {
      quotaLockTitle.textContent = "Crie sua conta para continuar convertendo";
    }
    if (quotaLockMessage) {
      quotaLockMessage.textContent = resetAt
        ? `Você usou as 3 conversões gratuitas desta semana. O próximo ciclo libera novas conversões em ${resetAt}. Cadastre-se para liberar +10 conversões semanais agora.`
        : "Você usou as 3 conversões gratuitas desta semana. Cadastre-se para liberar +10 conversões semanais agora.";
    }
    setQuotaLockActions("Criar conta", QUOTA_SIGNUP_URL, "Já tenho conta", QUOTA_LOGIN_URL);
    openQuotaLockOverlay(QUOTA_LOCK_VARIANT_ANONYMOUS);
  }

  function showRegisteredQuotaUpgradeOverlay(detail) {
    if (!quotaLockOverlay) {
      return;
    }
    const safeDetail = detail && typeof detail === "object" ? detail : {};
    const resetAt = formatResetAt(safeDetail.reset_at);
    if (quotaLockBadge) {
      quotaLockBadge.textContent = "Limite de 10 conversões atingido";
    }
    if (quotaLockTitle) {
      quotaLockTitle.textContent = "Seu plano gratuito chegou ao limite";
    }
    if (quotaLockMessage) {
      quotaLockMessage.textContent = resetAt
        ? `Você já usou as 10 conversões do plano gratuito neste ciclo. O próximo ciclo libera novas conversões em ${resetAt}. Para continuar agora, escolha um plano ou fale com o suporte.`
        : "Você já usou as 10 conversões do plano gratuito neste ciclo. Para continuar agora, escolha um plano ou fale com o suporte.";
    }
    setQuotaLockActions(
      "Ver planos",
      safeDetail.upgrade_url || QUOTA_PLANS_URL,
      "Falar com suporte",
      safeDetail.support_url || QUOTA_SUPPORT_URL,
    );
    openQuotaLockOverlay(QUOTA_LOCK_VARIANT_REGISTERED);
  }

  function hideQuotaLockOverlay() {
    if (!quotaLockOverlay) {
      return;
    }
    quotaLockOverlay.classList.remove("is-open");
    quotaLockOverlay.classList.add("hidden");
    delete quotaLockOverlay.dataset.variant;
    document.body.classList.remove("quota-locked");
    state.quotaLockVariant = null;
    setSelectedFileLabel();
  }

  function forceUnlockUi() {
    if (quotaLockOverlay) {
      quotaLockOverlay.classList.remove("is-open");
      quotaLockOverlay.classList.add("hidden");
      delete quotaLockOverlay.dataset.variant;
    }
    document.body.classList.remove("quota-locked");
    state.quotaLockVariant = null;
  }

  async function syncQuotaLockState() {
    if (!isQuotaLocked()) {
      return;
    }
    const quotaLockVariant =
      state.quotaLockVariant ||
      (quotaLockOverlay && quotaLockOverlay.dataset ? quotaLockOverlay.dataset.variant : "") ||
      "";
    const sessionState = await getSessionValidationState();
    if (sessionState === "invalid") {
      hideQuotaLockOverlay();
      clearUserToken();
      syncHeroAuthLinks();
      setStatus("Sua sessão expirou. Faça login novamente para continuar.", "error");
      return;
    }
    if (quotaLockVariant !== QUOTA_LOCK_VARIANT_ANONYMOUS) {
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

  function buildPagesLimitStatusHtml(detail) {
    const context =
      detail && typeof detail === "object" ? String(detail.ocr_context || "").trim().toLowerCase() : "";
    if (context === "scanned_pdf") {
      return (
        "Identificamos que este arquivo parece ser um documento escaneado. " +
        "Ele é um pouco grande para esse tipo de processamento. " +
        'Você pode dividir o PDF em arquivos menores e tentar novamente, ou <a href="./contato.html">enviar o arquivo para analisarmos</a>.'
      );
    }
    if (context === "unidentified_model_fallback") {
      return (
        "Não identificamos automaticamente o modelo deste extrato. " +
        "Tentamos ler o arquivo como um documento escaneado, mas ele é um pouco grande para esse tipo de processamento. " +
        'Você pode <a href="./contato.html">enviar o arquivo para analisarmos o modelo</a> ou dividir o PDF em arquivos menores e tentar novamente.'
      );
    }
    return (
      "Identificamos que o arquivo é um pouco grande para este processamento. " +
      'Você pode <a href="./contato.html">enviar o arquivo para analisarmos</a> ou dividir o PDF em arquivos menores e tentar novamente.'
    );
  }

  function updateProgressBarUI(percent) {
    if (!processingProgress || !processingProgressFill) {
      return;
    }
    const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
    processingProgressFill.style.width = `${safePercent.toFixed(1)}%`;
  }

  function animateProgressStep() {
    if (state.progressDisplay >= state.progressTarget) {
      state.progressFrame = null;
      return;
    }
    const distance = state.progressTarget - state.progressDisplay;
    const step = Math.max(0.25, Math.min(2.25, distance * 0.16));
    state.progressDisplay = Math.min(state.progressTarget, state.progressDisplay + step);
    updateProgressBarUI(state.progressDisplay);
    state.progressFrame = window.requestAnimationFrame(animateProgressStep);
  }

  function setProgressTarget(percent) {
    const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
    if (safePercent <= state.progressTarget) {
      return;
    }
    state.progressTarget = safePercent;
    if (state.progressFrame === null) {
      state.progressFrame = window.requestAnimationFrame(animateProgressStep);
    }
  }

  function startProgressDrift() {
    if (state.progressDriftTimer !== null) {
      window.clearInterval(state.progressDriftTimer);
    }
    state.progressDriftTimer = window.setInterval(() => {
      if (!state.isLoading) return;
      if (state.progressTarget >= 95) return;
      setProgressTarget(state.progressTarget + 0.8);
    }, 260);
  }

  function setStatusSmooth(message, kind) {
    const now = Date.now();
    const minGapMs = 420;
    const elapsed = now - Number(state.lastStatusAt || 0);
    const apply = () => {
      setStatus(message, kind || null);
      state.lastStatusAt = Date.now();
    };
    if (elapsed >= minGapMs) {
      apply();
      return;
    }
    if (state.statusPendingTimer !== null) {
      window.clearTimeout(state.statusPendingTimer);
      state.statusPendingTimer = null;
    }
    state.statusPendingTimer = window.setTimeout(apply, minGapMs - elapsed);
  }

  function showProgressBar() {
    if (!processingProgress) {
      return;
    }
    processingProgress.classList.remove("hidden");
    processingProgress.setAttribute("aria-hidden", "false");
  }

  function resetProgressBar() {
    if (state.progressFrame !== null) {
      window.cancelAnimationFrame(state.progressFrame);
      state.progressFrame = null;
    }
    if (state.progressDriftTimer !== null) {
      window.clearInterval(state.progressDriftTimer);
      state.progressDriftTimer = null;
    }
    if (state.statusPendingTimer !== null) {
      window.clearTimeout(state.statusPendingTimer);
      state.statusPendingTimer = null;
    }
    state.progressDisplay = 0;
    state.progressTarget = 0;
    state.lastStatusAt = 0;
    updateProgressBarUI(0);
    if (processingProgress) {
      processingProgress.classList.add("hidden");
      processingProgress.setAttribute("aria-hidden", "true");
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
      topAuthPrimaryLink.setAttribute("href", hasSession ? "./client-area.html" : PUBLIC_CONVERT_PATH);
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
      quota_mode: state.quotaMode,
      quota_remaining: state.quotaRemaining,
      quota_limit: state.quotaLimit,
      opening_balance_override: state.openingBalanceOverride,
      closing_balance_override: state.closingBalanceOverride,
      bank_branch_override: state.bankBranchOverride,
      account_number_override: state.accountNumberOverride,
      bank_code_override: state.bankCodeOverride,
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
    if (!isLoading) {
      resetProgressBar();
    }
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
    state.quotaRemaining = null;
    state.quotaLimit = null;
    state.openingBalanceOverride = null;
    state.openingBalanceManuallyEdited = false;
    state.closingBalanceOverride = null;
    state.closingBalanceManuallyEdited = false;
    state.bankBranchOverride = "";
    state.accountNumberOverride = "";
    state.bankCodeOverride = "";
    markChangedRow(null);
    if (addRowBtn) addRowBtn.disabled = true;
    setDownloadButtonsDisabled(true);
    reviewRows.innerHTML = "";
    kpis.innerHTML = "";
    reviewSection.classList.add("hidden");
    downloadSection.classList.add("hidden");
    if (analysisIdNode) analysisIdNode.textContent = "-";
    if (processingIdNode) processingIdNode.textContent = "-";
    updateQuotaRemainingValue(null, null);
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

  function toMoneyInputValue(value) {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric)) {
      return "0,00";
    }
    return numeric.toLocaleString("pt-BR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  function parsePtBrMoney(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      return null;
    }

    let normalized = raw.replace(/\s+/g, "");
    let negative = false;

    if (normalized.startsWith("(") && normalized.endsWith(")")) {
      negative = true;
      normalized = normalized.slice(1, -1);
    }
    if (/[dD]$/.test(normalized)) {
      negative = true;
      normalized = normalized.slice(0, -1);
    }
    if (normalized.startsWith("-")) {
      negative = true;
      normalized = normalized.slice(1);
    }
    if (normalized.startsWith("+")) {
      normalized = normalized.slice(1);
    }

    normalized = normalized.replace(/\./g, "").replace(",", ".");
    const parsed = Number(normalized);
    if (!Number.isFinite(parsed)) {
      return null;
    }
    return negative ? -parsed : parsed;
  }

  function normalizeDigits(value) {
    return String(value || "").replace(/\D+/g, "");
  }

  function normalizeDashedNumeric(value, maxLeft, maxRight) {
    const sanitized = String(value || "").replace(/[^\d-]/g, "");
    const firstHyphen = sanitized.indexOf("-");
    if (firstHyphen < 0) {
      return normalizeDigits(sanitized).slice(0, maxLeft);
    }
    const left = normalizeDigits(sanitized.slice(0, firstHyphen)).slice(0, maxLeft);
    const right = normalizeDigits(sanitized.slice(firstHyphen + 1)).slice(0, maxRight);
    if (!right) {
      return `${left}-`;
    }
    return `${left}-${right}`;
  }

  function normalizeBankBranchDisplay(value) {
    return normalizeDashedNumeric(value, 4, 1);
  }

  function normalizeAccountDisplay(value) {
    return normalizeDashedNumeric(value, 6, 1);
  }

  function normalizeTextToken(value) {
    const raw = String(value || "").trim().toLowerCase();
    if (!raw) {
      return "";
    }
    const folded = raw.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return folded.replace(/[^a-z0-9]+/g, " ").trim();
  }

  function isOpeningBalanceRow(row) {
    const normalized = normalizeTextToken(row && row.description);
    return normalized === "saldo anterior" || normalized === "saldo inicial";
  }

  function inferBankCodeFromLayout(layoutName) {
    const normalized = normalizeTextToken(String(layoutName || "").replace(/[_-]+/g, " "));
    if (!normalized) {
      return "";
    }
    for (const option of bankCodeOptions) {
      const candidates = [
        option.name,
        option.short_name,
        ...(Array.isArray(option.aliases) ? option.aliases : []),
      ];
      for (const candidate of candidates) {
        const token = normalizeTextToken(candidate);
        if (!token) continue;
        if (matchesWholeTokenPhrase(normalized, token)) {
          return String(option.code || "");
        }
      }
    }
    return "";
  }

  function matchesWholeTokenPhrase(source, phrase) {
    const normalizedSource = ` ${normalizeTextToken(source)} `;
    const normalizedPhrase = normalizeTextToken(phrase);
    if (!normalizedPhrase) {
      return false;
    }
    return normalizedSource.includes(` ${normalizedPhrase} `);
  }

  function resolveInitialBankCode(analysis, preferredOverride) {
    const overrideCode = normalizeDigits(preferredOverride || "").slice(0, 3);
    if (overrideCode) {
      return overrideCode;
    }
    const analysisBankCode = normalizeDigits((analysis && analysis.bank_code) || "").slice(0, 3);
    if (analysisBankCode) {
      return analysisBankCode;
    }
    return inferBankCodeFromLayout((analysis && analysis.layout_inference_name) || "");
  }

  function resolveOfxAccountType(analysis) {
    return String((analysis && analysis.ofx_account_type) || "").trim().toLowerCase();
  }

  function renderKpis(analysis) {
    const hasPreviewRows = Array.isArray(state.previewRows) && state.previewRows.length > 0;
    const derived = hasPreviewRows
      ? buildDerivedSummaryFromRows(state.previewRows)
      : {
          transactionsTotal: Number((analysis && analysis.transactions_total) || 0),
          totalInflows: Number((analysis && analysis.total_inflows) || 0),
          totalOutflows: Number((analysis && analysis.total_outflows) || 0),
          netTotal: Number((analysis && analysis.net_total) || 0),
        };
    const pagesConverted = resolveConvertedPages(analysis);
    const analysisOpeningBalance = Number((analysis && analysis.opening_balance) || 0);
    const analysisClosingBalance = Number(
      (analysis && analysis.closing_balance != null ? analysis.closing_balance : derived.netTotal) || 0
    );
    const openingBalance = Number.isFinite(Number(state.openingBalanceOverride))
      ? Number(state.openingBalanceOverride)
      : analysisOpeningBalance;
    const closingBalance = Number.isFinite(Number(state.closingBalanceOverride))
      ? Number(state.closingBalanceOverride)
      : analysisClosingBalance;
    const reviewWarningsMarkup = buildReviewWarningsMarkup(analysis);

    const inflows = formatCurrency(derived.totalInflows);
    const outflows = formatCurrency(derived.totalOutflows);
    const openingBalanceValue = toMoneyInputValue(openingBalance);
    const closingBalanceValue = toMoneyInputValue(closingBalance);
    const isOfxFlow = outputFormat === "ofx";
    const isCreditCardFlow = isOfxFlow && resolveOfxAccountType(analysis) === "credit_card";
    const bankBranchValue = normalizeBankBranchDisplay(state.bankBranchOverride);
    const accountNumberValue = normalizeAccountDisplay(state.accountNumberOverride);
    const bankCodeValue = String(state.bankCodeOverride || "").trim();
    const bankOptionsMarkup = bankCodeOptions.map((item) => {
      const selected = item.code === bankCodeValue ? " selected" : "";
      return `<option value="${item.code}"${selected}>${item.label}</option>`;
    }).join("");
    const ofxMetaRow = isOfxFlow
      ? `
      <div class="ofx-meta-row">
        <p class="kpi-hint">Banco, Agência e Conta</p>
        <div class="ofx-meta-fields">
          <select id="bank-code-select" class="kpi-edit-input">${bankOptionsMarkup}</select>
          <input id="bank-branch-input" class="kpi-edit-input" type="text" inputmode="numeric" placeholder="Agência (ex: 1234-5)" value="${bankBranchValue}" />
          <input id="account-number-input" class="kpi-edit-input" type="text" inputmode="numeric" placeholder="Conta (ex: 123456-7)" value="${accountNumberValue}" />
        </div>
      </div>
      `
      : "";

    kpis.innerHTML = `
      ${reviewWarningsMarkup}
      ${ofxMetaRow}
      <article class="kpi">
        <p class="kpi-label">Transações</p>
        <p class="kpi-value">${derived.transactionsTotal}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">Páginas convertidas</p>
        <p class="kpi-value">${pagesConverted}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">Entradas</p>
        <p class="kpi-value">${inflows}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">Saídas</p>
        <p class="kpi-value">${outflows}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">Saldo anterior</p>
        ${
          isOfxFlow
            ? `<p class="kpi-value-editable"><input id="opening-balance-input" class="kpi-edit-input" type="text" inputmode="decimal" value="${openingBalanceValue}" /></p>`
            : `<p class="kpi-value">${formatCurrency(openingBalance)}</p>`
        }
      </article>
      <article class="kpi">
        <p class="kpi-label">Saldo final</p>
        ${
          isOfxFlow
            ? `<p class="kpi-value-editable"><input id="closing-balance-input" class="kpi-edit-input" type="text" inputmode="decimal" value="${closingBalanceValue}" /></p>`
            : `<p class="kpi-value">${formatCurrency(closingBalance)}</p>`
        }
      </article>
    `;
  }

  function renderKpisLegacy(analysis) {
    const hasPreviewRows = Array.isArray(state.previewRows) && state.previewRows.length > 0;
    const derived = hasPreviewRows
      ? buildDerivedSummaryFromRows(state.previewRows)
      : {
          transactionsTotal: Number((analysis && analysis.transactions_total) || 0),
          totalInflows: Number((analysis && analysis.total_inflows) || 0),
          totalOutflows: Number((analysis && analysis.total_outflows) || 0),
          netTotal: Number((analysis && analysis.net_total) || 0),
        };
    const pagesConverted = resolveConvertedPages(analysis);
    const analysisOpeningBalance = Number((analysis && analysis.opening_balance) || 0);
    const analysisClosingBalance = Number(
      (analysis && analysis.closing_balance != null ? analysis.closing_balance : derived.netTotal) || 0
    );
    const openingBalance = Number.isFinite(Number(state.openingBalanceOverride))
      ? Number(state.openingBalanceOverride)
      : analysisOpeningBalance;
    const closingBalance = Number.isFinite(Number(state.closingBalanceOverride))
      ? Number(state.closingBalanceOverride)
      : analysisClosingBalance;
    const reviewWarningsMarkup = buildReviewWarningsMarkup(analysis);
    const inflows = formatCurrency(derived.totalInflows);
    const outflows = formatCurrency(derived.totalOutflows);
    const openingBalanceValue = toMoneyInputValue(openingBalance);
    const closingBalanceValue = toMoneyInputValue(closingBalance);
    const isOfxFlow = outputFormat === "ofx";
    const isCreditCardFlow = isOfxFlow && resolveOfxAccountType(analysis) === "credit_card";
    const bankBranchValue = normalizeBankBranchDisplay(state.bankBranchOverride);
    const accountNumberValue = normalizeAccountDisplay(state.accountNumberOverride);
    const bankCodeValue = String(state.bankCodeOverride || "").trim();
    const bankOptionsMarkup = bankCodeOptions.map((item) => {
      const selected = item.code === bankCodeValue ? " selected" : "";
      return `<option value="${item.code}"${selected}>${item.label}</option>`;
    }).join("");
    const ofxMetaRow = isCreditCardFlow
      ? `
      <div class="ofx-meta-row">
        <p class="kpi-hint">Fatura de cartao detectada</p>
        <div class="ofx-meta-fields">
          <p class="kpi-hint">OFX de cartao nao precisa de banco, agencia ou conta para exportacao.</p>
        </div>
      </div>
      `
      : isOfxFlow
        ? `
        <div class="ofx-meta-row">
          <p class="kpi-hint">Banco, Agencia e Conta</p>
          <div class="ofx-meta-fields">
            <select id="bank-code-select" class="kpi-edit-input">${bankOptionsMarkup}</select>
            <input id="bank-branch-input" class="kpi-edit-input" type="text" inputmode="numeric" placeholder="Agencia (ex: 1234-5)" value="${bankBranchValue}" />
            <input id="account-number-input" class="kpi-edit-input" type="text" inputmode="numeric" placeholder="Conta (ex: 123456-7)" value="${accountNumberValue}" />
          </div>
        </div>
        `
        : "";
    const inflowsLabel = isCreditCardFlow ? "Pagamentos e creditos" : "Entradas";
    const outflowsLabel = isCreditCardFlow ? "Despesas" : "Saidas";
    const openingBalanceLabel = isCreditCardFlow ? "Saldo anterior da fatura" : "Saldo anterior";
    const closingBalanceLabel = isCreditCardFlow ? "Valor final da fatura" : "Saldo final";

    kpis.innerHTML = `
      ${reviewWarningsMarkup}
      ${ofxMetaRow}
      <article class="kpi">
        <p class="kpi-label">Transacoes</p>
        <p class="kpi-value">${derived.transactionsTotal}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">Paginas convertidas</p>
        <p class="kpi-value">${pagesConverted}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">${inflowsLabel}</p>
        <p class="kpi-value">${inflows}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">${outflowsLabel}</p>
        <p class="kpi-value">${outflows}</p>
      </article>
      <article class="kpi">
        <p class="kpi-label">${openingBalanceLabel}</p>
        ${
          isOfxFlow
            ? `<p class="kpi-value-editable"><input id="opening-balance-input" class="kpi-edit-input" type="text" inputmode="decimal" value="${openingBalanceValue}" /></p>`
            : `<p class="kpi-value">${formatCurrency(openingBalance)}</p>`
        }
      </article>
      <article class="kpi">
        <p class="kpi-label">${closingBalanceLabel}</p>
        ${
          isOfxFlow
            ? `<p class="kpi-value-editable"><input id="closing-balance-input" class="kpi-edit-input" type="text" inputmode="decimal" value="${closingBalanceValue}" /></p>`
            : `<p class="kpi-value">${formatCurrency(closingBalance)}</p>`
        }
      </article>
    `;
  }

  function buildReviewWarningsMarkup(analysis) {
    const warnings = collectReviewWarnings(analysis);
    if (!warnings.length) {
      return "";
    }
    const items = warnings.map((item) => `<li>${escapeAttr(item)}</li>`).join("");
    return `
      <section class="review-warning-card" role="status" aria-live="polite">
        <h3 class="review-warning-title">Atenção na revisão</h3>
        <ul class="review-warning-list">${items}</ul>
      </section>
    `;
  }

  function collectReviewWarnings(analysis) {
    const list = [];
    const metrics = analysis && typeof analysis === "object" ? analysis.pdf_processing_metrics : null;
    if (!metrics || typeof metrics !== "object") {
      return list;
    }
    const txCount = Number(analysis.transactions_total || 0);
    const pageCount = Number(metrics.page_count || 0);
    const parser = String(metrics.selected_parser || "").trim().toLowerCase();
    const layout = String(analysis.layout_inference_name || "").trim().toLowerCase();
    const confidenceBand = String(metrics.confidence_band || "").trim().toLowerCase();
    const exportRecommendation = String(metrics.export_recommendation || "").trim().toLowerCase();
    const balanceFailed = Number(metrics.balance_consistency_failed || 0);

    if (
      pageCount >= 2 &&
      txCount <= Math.max(3, pageCount) &&
      (layout === "generic_statement_ptbr" || parser === "grouped")
    ) {
      list.push(
        `Leitura parcial detectada: ${txCount} transações em ${pageCount} páginas. Revise com atenção antes de exportar.`
      );
    }
    if (balanceFailed > 0) {
      list.push(
        `Saldo linha a linha com inconsistências: ${balanceFailed} lançamento(s) não bateram no cálculo de saldo.`
      );
    }
    if (confidenceBand === "low" || exportRecommendation === "review_recommended") {
      list.push("Recomendamos revisar os lançamentos antes de baixar o arquivo.");
    }
    return list;
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

  function getRunningBalanceAmount(row) {
    const value = row && Object.prototype.hasOwnProperty.call(row, "running_balance") ? row.running_balance : null;
    if (value === null || value === undefined || value === "") {
      return null;
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function roundMoney(value) {
    return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
  }

  function buildDisplayedRunningBalances(rows) {
    const displayed = [];
    const opening = Number.isFinite(Number(state.openingBalanceOverride))
      ? Number(state.openingBalanceOverride)
      : Number(state.analysisSnapshot && state.analysisSnapshot.opening_balance);
    let cursor = Number.isFinite(opening) ? opening : null;

    for (const row of rows || []) {
      const explicit = getRunningBalanceAmount(row);
      if (explicit !== null) {
        cursor = explicit;
        displayed.push(explicit);
        continue;
      }

      if (cursor === null) {
        displayed.push(null);
        continue;
      }

      const amount = Number(row && row.amount);
      const safeAmount = Number.isFinite(amount) ? amount : 0;
      cursor = roundMoney(cursor + safeAmount);
      displayed.push(cursor);
    }

    return displayed;
  }

  function buildDerivedSummaryFromRows(rows) {
    const activeRows = (rows || []).filter(
      (row) => row && row.is_deleted !== true && !isOpeningBalanceRow(row)
    );
    const totalInflows = roundMoney(
      activeRows.reduce((acc, row) => {
        const amount = Number(row && row.amount);
        return Number.isFinite(amount) && amount > 0 ? acc + amount : acc;
      }, 0)
    );
    const totalOutflows = roundMoney(
      activeRows.reduce((acc, row) => {
        const amount = Number(row && row.amount);
        return Number.isFinite(amount) && amount < 0 ? acc + amount : acc;
      }, 0)
    );
    return {
      transactionsTotal: activeRows.length,
      totalInflows,
      totalOutflows,
      netTotal: roundMoney(totalInflows + totalOutflows),
    };
  }

  function syncOpeningBalanceFromPayload(payload) {
    if (state.openingBalanceManuallyEdited) {
      return;
    }
    const openingFromPayload = payload && payload.opening_balance != null ? Number(payload.opening_balance) : NaN;
    if (Number.isFinite(openingFromPayload)) {
      state.openingBalanceOverride = openingFromPayload;
    }
  }

  function syncClosingBalanceFromPayload(payload) {
    if (state.closingBalanceManuallyEdited) {
      return;
    }
    const closingFromPayload =
      payload && payload.closing_balance != null
        ? Number(payload.closing_balance)
        : payload
          ? Number(payload.net_total)
          : NaN;
    if (Number.isFinite(closingFromPayload)) {
      state.closingBalanceOverride = closingFromPayload;
    }
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
      if (payload.opening_balance != null) {
        state.analysisSnapshot.opening_balance = Number(payload.opening_balance);
      }
      if (payload.closing_balance != null) {
        state.analysisSnapshot.closing_balance = Number(payload.closing_balance);
      }
      syncOpeningBalanceFromPayload(payload);
      syncClosingBalanceFromPayload(payload);
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
      if (payload.opening_balance != null) {
        state.analysisSnapshot.opening_balance = Number(payload.opening_balance);
      }
      if (payload.closing_balance != null) {
        state.analysisSnapshot.closing_balance = Number(payload.closing_balance);
      }
      syncOpeningBalanceFromPayload(payload);
      syncClosingBalanceFromPayload(payload);
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
    focusEditingDateInput();
    persistCurrentViewState();
  }

  function focusEditingDateInput() {
    window.requestAnimationFrame(() => {
      const dateInput = reviewRows.querySelector('input[data-edit-field="date"]');
      if (!(dateInput instanceof HTMLInputElement)) {
        return;
      }
      dateInput.focus();
      const valueLength = dateInput.value.length;
      dateInput.setSelectionRange(valueLength, valueLength);
    });
  }

  function startEditingRow(rowId) {
    const row = state.previewRows.find((item) => item.rowId === rowId);
    if (!row) {
      return;
    }
    state.editingRowId = rowId;
    state.editDraft = {
      date: toIsoDateInputValue(row.date),
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
      setStatus("Data inválida. Use o calendário ou o formato dd-mm-aaaa.", "error");
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
        if (payload.opening_balance != null) {
          state.analysisSnapshot.opening_balance = Number(payload.opening_balance);
        }
        if (payload.closing_balance != null) {
          state.analysisSnapshot.closing_balance = Number(payload.closing_balance);
        }
        syncOpeningBalanceFromPayload(payload);
        syncClosingBalanceFromPayload(payload);
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
    const rows = (state.previewRows || []).filter((row) => !isOpeningBalanceRow(row));
    if (!rows || rows.length === 0) {
      reviewRows.innerHTML = '<tr><td colspan="6">Nenhuma transação para exibir.</td></tr>';
      return;
    }

    const displayedRunningBalances = buildDisplayedRunningBalances(rows);
    reviewRows.innerHTML = rows
      .map((row, index) => {
        const rowClass =
          row.rowId === state.lastChangedRowId
            ? state.lastChangedRowKind === "new"
              ? "row-new"
              : "row-changed"
            : "";
        const rowDeleted = Boolean(row.is_deleted);
        const isEditing = row.rowId === state.editingRowId && state.editDraft;
        if (isEditing) {
          const editDateValue = toIsoDateInputValue(state.editDraft.date);
          return `
          <tr class="${rowClass}">
            <td><input class="cell-input cell-input-date" type="date" data-edit-field="date" autocomplete="off" value="${escapeAttr(editDateValue)}" /></td>
            <td><input class="cell-input cell-input-description" data-edit-field="description" value="${escapeAttr(state.editDraft.description)}" /></td>
            <td><input class="cell-input cell-input-money" data-edit-field="credit" inputmode="decimal" placeholder="0,00" value="${escapeAttr(state.editDraft.credit)}" /></td>
            <td><input class="cell-input cell-input-money" data-edit-field="debit" inputmode="decimal" placeholder="0,00" value="${escapeAttr(state.editDraft.debit)}" /></td>
            <td><span class="amount-empty">—</span></td>
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
        const warningTypes = Array.isArray(row.warning_types)
          ? row.warning_types.filter((value) => String(value || "").trim().toLowerCase() !== "layout_fallback")
          : [];
        const hasWarning = warningTypes.length > 0;
        const creditAmount = getCreditAmount(row);
        const debitAmount = getDebitAmount(row);
        const creditMarkup = creditAmount !== null
          ? `<span class="amount-credit">${formatCurrency(creditAmount)}</span>`
          : '<span class="amount-empty">—</span>';
        const debitMarkup = debitAmount !== null
          ? `<span class="amount-debit">${formatCurrency(debitAmount)}</span>`
          : '<span class="amount-empty">—</span>';
        const runningBalanceAmount = displayedRunningBalances[index];
        const runningBalanceMarkup = runningBalanceAmount !== null
          ? `<span class="amount-balance">${formatCurrency(runningBalanceAmount)}</span>`
          : '<span class="amount-empty">—</span>';
        const descriptionText = String(row.description || "").trim();
        const descriptionCellText = descriptionText || "-";
        const descriptionTitleAttr = descriptionText ? ` title="${escapeAttr(descriptionText)}"` : "";
        const warningBadge = hasWarning
          ? `<span class="row-warning-badge" title="${escapeAttr(warningTypes.map(mapWarningTypeExplanation).join(" | "))}">${escapeAttr(mapWarningTypeLabel(warningTypes[0]))}</span>`
          : "";
        return `
          <tr class="${rowClass} ${rowDeleted ? "row-deleted" : ""} ${hasWarning ? "row-has-warning" : ""}">
            <td>${formatDate(row.date)}</td>
            <td${descriptionTitleAttr}>${escapeAttr(descriptionCellText)}${warningBadge}</td>
            <td>${creditMarkup}</td>
            <td>${debitMarkup}</td>
            <td>${runningBalanceMarkup}</td>
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

  function mapWarningTypeLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    if (key === "balance_consistency_failed") {
      return "Saldo inconsistente";
    }
    if (key === "layout_fallback") {
      return "Modelo generico";
    }
    if (key === "manual_review_recommended") {
      return "Revisao manual";
    }
    if (key === "textract_layout_inferred") {
      return "Layout inferido";
    }
    if (key === "textract_table_row_candidate") {
      return "Linha inferida";
    }
    return "Linha com alerta";
  }

  function mapWarningTypeExplanation(value) {
    const key = String(value || "").trim().toLowerCase();
    if (key === "balance_consistency_failed") {
      return "O saldo calculado para esta linha nao bateu com a consistencia esperada do extrato.";
    }
    if (key === "layout_fallback") {
      return "O PDF foi lido com o modelo generico porque o layout especifico do extrato nao foi identificado com confianca.";
    }
    if (key === "manual_review_recommended") {
      return "Esta linha foi marcada para revisao manual antes da exportacao.";
    }
    if (key === "textract_layout_inferred") {
      return "Esta linha veio de uma inferencia automatica de layout e pode precisar de conferencia.";
    }
    if (key === "textract_table_row_candidate") {
      return "Esta linha foi montada a partir de uma extracao tabular automatica e deve ser conferida.";
    }
    return "Esta linha recebeu um alerta e precisa de revisao antes da exportacao.";
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
    const restoredOpeningBalance = Number(viewState.opening_balance_override);
    const restoredClosingBalance = Number(viewState.closing_balance_override);
    state.openingBalanceOverride = Number.isFinite(restoredOpeningBalance) ? restoredOpeningBalance : null;
    state.openingBalanceManuallyEdited = Number.isFinite(restoredOpeningBalance);
    state.closingBalanceOverride = Number.isFinite(restoredClosingBalance) ? restoredClosingBalance : null;
    state.closingBalanceManuallyEdited = Number.isFinite(restoredClosingBalance);
    state.bankBranchOverride = normalizeBankBranchDisplay(viewState.bank_branch_override || "");
    state.accountNumberOverride = normalizeAccountDisplay(viewState.account_number_override || "");
    state.bankCodeOverride = resolveInitialBankCode(analysis, viewState.bank_code_override || "");
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
    state.quotaMode = normalizeQuotaMode(viewState.quota_mode || inferQuotaModeFromText(viewState.quota_text));
    const restoredQuotaRemaining = Number(viewState.quota_remaining);
    const restoredQuotaLimit = Number(viewState.quota_limit);
    state.quotaRemaining = Number.isFinite(restoredQuotaRemaining) ? restoredQuotaRemaining : null;
    state.quotaLimit = Number.isFinite(restoredQuotaLimit) ? restoredQuotaLimit : null;
    if (state.quotaRemaining !== null && state.quotaLimit !== null) {
      updateQuotaRemainingValue(state.quotaRemaining, state.quotaLimit);
    } else {
      updateQuotaRemainingLabel();
      quotaRemainingNode.textContent = viewState.quota_text || "-";
    }

    reviewSection.classList.remove("hidden");
    downloadSection.classList.remove("hidden");
    if (addRowBtn) addRowBtn.disabled = false;

    const canDownload = Boolean(state.analysisId || state.processingId);
    setDownloadButtonsDisabled(!canDownload);

    setStatus("Sessão restaurada. Você pode continuar o download.", "success");
  }

  async function postConvert(formData, options) {
    const onStatusEvent = options && typeof options.onStatusEvent === "function" ? options.onStatusEvent : null;
    const token = getUserToken();
    const headers = {
      Accept: "text/event-stream",
      ...(buildOptionalAuthHeaders(token) || {}),
    };
    const response = await fetch(`${apiBase}/api/conversions/upload`, {
      method: "POST",
      credentials: "include",
      headers,
      body: formData,
    });
    const contentType = String(response.headers.get("content-type") || "").toLowerCase();

    if (contentType.includes("text/event-stream") && response.body) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let completedPayload = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() || "";
        for (const rawEvent of events) {
          const dataLine = rawEvent
            .split("\n")
            .find(function (line) {
              return line.startsWith("data: ");
            });
          if (!dataLine) continue;
          const event = JSON.parse(dataLine.slice(6));
          if (onStatusEvent) onStatusEvent(event);
          if (event.stage === "failed") {
            const failedCode = String(event.code || "processing_failed");
            throw buildApiError(resolveSseFailedStatus(failedCode), {
              code: failedCode,
              message: String(event.message || "Falha ao converter arquivo."),
              retryable: Boolean(event.retryable),
              identity_type: event.identity_type || null,
              quota_mode: event.quota_mode || null,
              quota_limit: event.quota_limit || null,
              quota_remaining: event.quota_remaining || null,
              reset_at: event.reset_at || null,
              upgrade_url: event.upgrade_url || null,
              support_url: event.support_url || null,
              plan_name: event.plan_name || null,
              ocr_context: event.ocr_context || null,
              pages_count: event.pages_count || null,
              max_pages_per_file: event.max_pages_per_file || null,
            });
          }
          if (event.stage === "completed") {
            completedPayload = event.convertPayload || null;
          }
        }
      }

      if (completedPayload && typeof completedPayload === "object") {
        return completedPayload;
      }
      throw buildApiError(500, "Conversão finalizada sem dados de retorno.");
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw buildApiError(response.status, payload.detail || "Falha ao converter arquivo.");
    }
    return payload;
  }

  function setUploadLimitsText(maxUploadBytes, maxPagesPerFile) {
    if (!uploadLimitsText) return;
    const textMb = Number(maxUploadBytes || 0) / (1024 * 1024);
    const safeTextMb = Number.isFinite(textMb) && textMb > 0 ? Math.max(textMb, TEXT_PDF_MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)) : TEXT_PDF_MAX_UPLOAD_SIZE_BYTES / (1024 * 1024);
    void maxPagesPerFile;
    uploadLimitsText.textContent = `PDF com texto: até ${safeTextMb.toFixed(0)} MB.`;
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
        state.quotaMode = "conversion";
        updateQuotaRemainingLabel();
        setUploadLimitsText(5 * 1024 * 1024, 6);
        return;
      }
      const me = await response.json().catch(() => ({}));
      state.quotaMode = normalizeQuotaMode(me.quota_mode);
      if (state.quotaRemaining !== null && state.quotaLimit !== null) {
        updateQuotaRemainingValue(state.quotaRemaining, state.quotaLimit);
      } else {
        const parsedQuota = parseQuotaNumbersFromText(quotaRemainingNode ? quotaRemainingNode.textContent : "");
        if (parsedQuota) {
          state.quotaRemaining = parsedQuota.remaining;
          state.quotaLimit = parsedQuota.limit;
          updateQuotaRemainingValue(state.quotaRemaining, state.quotaLimit);
        } else {
          updateQuotaRemainingLabel();
        }
      }
      setUploadLimitsText(Number(me.max_upload_size_bytes || 5 * 1024 * 1024), Number(me.max_pages_per_file || 10));
    } catch (_error) {
      state.quotaMode = "conversion";
      updateQuotaRemainingLabel();
      setUploadLimitsText(5 * 1024 * 1024, 6);
    }
  }

  async function loadBankOptions() {
    try {
      const response = await fetch(`${apiBase}/banks`, {
        method: "GET",
        credentials: "include",
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const rows = payload && Array.isArray(payload.banks) ? payload.banks : [];
      const options = rows
        .map((item) => ({
          code: normalizeDigits(item.code || "").slice(0, 3),
          label: String(item.label || "").trim(),
          name: String(item.name || "").trim(),
          short_name: String(item.short_name || "").trim(),
          aliases: Array.isArray(item.aliases) ? item.aliases.map((value) => String(value || "").trim()).filter(Boolean) : [],
        }))
        .filter((item) => item.code && item.label);
      if (!options.length) {
        return;
      }
      bankCodeOptions = [{ code: "", label: "Selecione o banco", name: "", short_name: "", aliases: [] }, ...options];
      if ((!state.bankCodeOverride || !String(state.bankCodeOverride).trim()) && state.analysisSnapshot) {
        state.bankCodeOverride = resolveInitialBankCode(state.analysisSnapshot, state.bankCodeOverride);
      }
      if (state.analysisSnapshot) {
        renderKpis(state.analysisSnapshot);
      }
    } catch (_error) {
      // Keep fallback option when the catalog endpoint is unavailable.
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
    showProgressBar();
    setProgressTarget(3);
    startProgressDrift();

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

      const payload = await postConvert(formData, {
        onStatusEvent: function (event) {
          const stage = String((event && event.stage) || "").trim();
          const message = String((event && event.message) || "").trim();
          const progress = Number((event && event.progress) || 0);
          if (Number.isFinite(progress) && progress > 0) {
            setProgressTarget(progress);
          }
          if (!stage || !message) return;
          if (
            stage === "document_processing" &&
            Number.isFinite(Number(event.currentPage)) &&
            Number.isFinite(Number(event.totalPages))
          ) {
            setStatusSmooth(`Lendo página ${Number(event.currentPage)} de ${Number(event.totalPages)}...`, null);
            return;
          }
          setStatusSmooth(message, null);
        },
      });
      state.quotaMode = normalizeQuotaMode(payload.quota_mode);

      const analysis = payload.analysis;
      state.analysisId = analysis.analysis_id;
      state.processingId = payload.processing_id || analysis.analysis_id;
      state.analysisSnapshot = { ...analysis };
      state.openingBalanceManuallyEdited = false;
      state.openingBalanceOverride = Number(analysis.opening_balance != null ? analysis.opening_balance : 0);
      state.closingBalanceManuallyEdited = false;
      state.closingBalanceOverride = Number(
        analysis.closing_balance != null ? analysis.closing_balance : analysis.net_total || 0
      );
      state.bankBranchOverride = normalizeBankBranchDisplay(analysis.bank_branch || "");
      state.accountNumberOverride = normalizeAccountDisplay(analysis.account_number || "");
      state.bankCodeOverride = resolveInitialBankCode(analysis, state.bankCodeOverride);
      markChangedRow(null);
      if (addRowBtn) addRowBtn.disabled = false;

      setPreviewRows(analysis.preview_transactions || []);
      setOriginalRows(analysis.preview_transactions || []);
      renderKpis(analysis);
      renderRows();

      if (analysisIdNode) analysisIdNode.textContent = analysis.analysis_id || "-";
      if (processingIdNode) processingIdNode.textContent = state.processingId || "-";
      state.quotaRemaining = Number(payload.quota_remaining);
      state.quotaLimit = Number(payload.quota_limit);
      updateQuotaRemainingValue(state.quotaRemaining, state.quotaLimit);

      reviewSection.classList.remove("hidden");
      downloadSection.classList.remove("hidden");
      const canDownload = Boolean(state.analysisId);
      setDownloadButtonsDisabled(!canDownload);

      persistCurrentViewState();

      setStatus("Conversão concluída. Revise os dados e baixe o relatório.", "success");
      setProgressTarget(100);
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
        const detailIdentityType =
          detail && typeof detail === "object" ? String(detail.identity_type || "").trim().toLowerCase() : "";
        const detailQuotaMode =
          detail && typeof detail === "object" ? String(detail.quota_mode || "").trim().toLowerCase() : "";
        const shouldShowAnonymousQuotaLock =
          detailIdentityType === "anonymous" || (!detailIdentityType && !getUserToken());
        if (shouldShowAnonymousQuotaLock) {
          showQuotaLockOverlay(detail);
          setStatus("Você atingiu o limite gratuito desta semana.", "error");
          return;
        }
        if (detailIdentityType === "user" && (!detailQuotaMode || detailQuotaMode === "conversion")) {
          showRegisteredQuotaUpgradeOverlay(detail);
          setStatus("Seu plano gratuito chegou ao limite. Veja os planos ou fale com o suporte.", "error");
          return;
        }
      }
      if (status === 429 && code === "monthly_pages_quota_exceeded") {
        setStatus("Você atingiu o limite mensal de páginas do seu plano.", "error");
        return;
      }
      if (status === 400 && code === "pages_limit_exceeded") {
        setStatusHtml(buildPagesLimitStatusHtml(detail), "error");
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

  function resolveDownloadConfig(fileFormat) {
    if (fileFormat === "excel") {
      const processingId = state.processingId || state.analysisId;
      return {
        format: "excel",
        reportFormat: "xlsx",
        targetId: processingId,
        endpoint: processingId ? `${apiBase}/convert-report/${processingId}` : "",
        fileName: buildFallbackDownloadFilename("xlsx"),
        errorMessage: "Falha ao baixar Excel.",
        networkErrorMessage: "Falha de rede ao baixar Excel.",
      };
    }
    const processingId = state.processingId || state.analysisId;
    return {
      format: "ofx",
      reportFormat: "ofx",
      targetId: processingId,
      endpoint: processingId ? `${apiBase}/convert-report/${processingId}` : "",
      fileName: buildFallbackDownloadFilename("ofx"),
      errorMessage: "Falha ao baixar OFX.",
      networkErrorMessage: "Falha de rede ao baixar OFX.",
    };
  }

  async function runDownloadReport(formatOverride) {
    const requestedFormat = String(formatOverride || outputFormat).trim().toLowerCase() === "excel" ? "excel" : "ofx";
    const downloadConfig = resolveDownloadConfig(requestedFormat);
    if (!downloadConfig.targetId) {
      setStatus("Converta um arquivo antes de baixar.", "error");
      return;
    }
    const query = buildIdentityQueryParams();
    const token = getUserToken();
    const headers = buildOptionalAuthHeaders(token);

    try {
      setStatus("Preparando download...", null);
      if (downloadConfig.reportFormat) {
        query.set("format", downloadConfig.reportFormat);
      }
      if (requestedFormat === "ofx") {
        const openingBalance = Number(state.openingBalanceOverride);
        const closingBalance = Number(state.closingBalanceOverride);
        const isCreditCardFlow = resolveOfxAccountType(state.analysisSnapshot) === "credit_card";
        const bankBranch = isCreditCardFlow ? "" : normalizeDigits(state.bankBranchOverride);
        const accountNumber = isCreditCardFlow ? "" : normalizeDigits(state.accountNumberOverride);
        const bankCode = isCreditCardFlow ? "" : normalizeDigits(state.bankCodeOverride || "").slice(0, 3);
        const settingsQuery = buildIdentityQueryParams();
        const settingsResponse = await fetch(`${apiBase}/convert-edits/${downloadConfig.targetId}?${settingsQuery.toString()}`, {
          method: "POST",
          credentials: "include",
          headers: {
            ...(headers || {}),
            "content-type": "application/json",
          },
          body: JSON.stringify({
            edits: [],
            expected_updated_at: state.analysisSnapshot ? state.analysisSnapshot.updated_at || null : null,
            opening_balance: Number.isFinite(openingBalance) ? Number(openingBalance.toFixed(2)) : null,
            closing_balance: Number.isFinite(closingBalance) ? Number(closingBalance.toFixed(2)) : null,
            bank_branch: bankBranch || null,
            account_number: accountNumber || null,
            bank_code: bankCode || null,
          }),
        });
        if (!settingsResponse.ok) {
          const settingsPayload = await settingsResponse.json().catch(() => ({}));
          const detail = settingsPayload && typeof settingsPayload === "object" ? settingsPayload.detail : null;
          const message = String(detail || "Falha ao atualizar os dados do OFX.");
          setStatus(message, "error");
          return;
        }
        const settingsPayload = await settingsResponse.json().catch(() => ({}));
        if (settingsPayload && typeof settingsPayload.updated_at === "string" && state.analysisSnapshot) {
          state.analysisSnapshot.updated_at = settingsPayload.updated_at;
          persistCurrentViewState();
        }
      }
      const url = `${downloadConfig.endpoint}?${query.toString()}`;
      const response = await fetch(url, {
        method: "GET",
        credentials: "include",
        ...(headers ? { headers } : {}),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = payload && typeof payload === "object" ? payload.detail : null;
        const message = String(detail || downloadConfig.errorMessage);
        setStatus(message, "error");
        return;
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      const contentDisposition = response.headers.get("content-disposition");
      const serverFileName = parseDownloadFilenameFromContentDisposition(contentDisposition);
      anchor.download = serverFileName || downloadConfig.fileName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
      setStatus("Download iniciado.", "success");
    } catch (_error) {
      setStatus(downloadConfig.networkErrorMessage, "error");
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
    if (field === "date") {
      if (target.type === "date") {
        updateEditDraft(field, target.value);
        return;
      }
      const masked = applyDateInputMask(target.value);
      if (masked !== target.value) {
        target.value = masked;
      }
      updateEditDraft(field, masked);
      return;
    }
    updateEditDraft(field, target.value);
  });
  kpis.addEventListener("input", function (event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) {
      return;
    }
    if (target.id === "bank-branch-input") {
      target.value = normalizeBankBranchDisplay(target.value);
      return;
    }
    if (target.id === "account-number-input") {
      target.value = normalizeAccountDisplay(target.value);
    }
  });
  kpis.addEventListener("change", function (event) {
    const target = event.target;
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) {
      return;
    }
    if (target.id === "bank-branch-input") {
      state.bankBranchOverride = normalizeBankBranchDisplay(target.value);
      target.value = state.bankBranchOverride;
      persistCurrentViewState();
      setStatus("Agência atualizada para o próximo download OFX.", "success");
      return;
    }
    if (target.id === "account-number-input") {
      state.accountNumberOverride = normalizeAccountDisplay(target.value);
      target.value = state.accountNumberOverride;
      persistCurrentViewState();
      setStatus("Conta atualizada para o próximo download OFX.", "success");
      return;
    }
    if (target.id === "bank-code-select") {
      state.bankCodeOverride = normalizeDigits(target.value).slice(0, 3);
      persistCurrentViewState();
      setStatus(
        state.bankCodeOverride
          ? "Banco atualizado para o próximo download OFX."
          : "Banco automático ativado para o próximo download OFX.",
        "success",
      );
      return;
    }
    if (target.id === "opening-balance-input") {
      const parsed = parsePtBrMoney(target.value);
      if (parsed === null) {
        setStatus("Saldo anterior inválido. Use formato como 56.276,06", "error");
        target.value = toMoneyInputValue(
          Number.isFinite(Number(state.openingBalanceOverride))
            ? Number(state.openingBalanceOverride)
            : Number((state.analysisSnapshot && state.analysisSnapshot.opening_balance) || 0),
        );
        return;
      }
      state.openingBalanceOverride = parsed;
      state.openingBalanceManuallyEdited = true;
      target.value = toMoneyInputValue(parsed);
      persistCurrentViewState();
      renderRows();
      setStatus("Saldo anterior atualizado para a revisão e o próximo download OFX.", "success");
      return;
    }
    if (target.id !== "closing-balance-input") {
      return;
    }
    const parsed = parsePtBrMoney(target.value);
    if (parsed === null) {
      setStatus("Saldo final inválido. Use formato como 56.276,06", "error");
      target.value = toMoneyInputValue(
        Number.isFinite(Number(state.closingBalanceOverride))
          ? Number(state.closingBalanceOverride)
          : Number(
              (state.analysisSnapshot &&
                (state.analysisSnapshot.closing_balance != null
                  ? state.analysisSnapshot.closing_balance
                  : state.analysisSnapshot.net_total)) ||
                0
            ),
      );
      return;
    }
    state.closingBalanceOverride = parsed;
    state.closingBalanceManuallyEdited = true;
    target.value = toMoneyInputValue(parsed);
    persistCurrentViewState();
    setStatus("Saldo final atualizado para o próximo download OFX.", "success");
  });
  convertBtn.addEventListener("click", runConvert);
  if (addRowBtn) addRowBtn.addEventListener("click", startInsertRow);
  if (hasDualDownloadButtons) {
    downloadOfxBtn.addEventListener("click", function () {
      void runDownloadReport("ofx");
    });
    downloadExcelBtn.addEventListener("click", function () {
      void runDownloadReport("excel");
    });
  } else if (defaultDownloadBtn) {
    defaultDownloadBtn.addEventListener("click", function () {
      void runDownloadReport(outputFormat);
    });
  }
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
  async function initializePage() {
    updateQuotaRemainingLabel();
    forceUnlockUi();
    setSelectedFileLabel();
    const didForceLogout = consumeLogoutQueryFlag();
    if (!(await enforceAuthenticatedAccess())) {
      return;
    }
    syncHeroAuthLinks();
    void hydrateTopAccountEmail();
    void loadBankOptions();
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
    if (didForceLogout && !requireAuthAccess) {
      setStatus("Sessão encerrada. Você está no modo gratuito (anônimo).", "success");
    }
  }

  void initializePage();
})();




