(function () {
  "use strict";

  // Disable this experiment to restore the existing conversion survey.
  const ENABLED = true;
  const COURSE_PATH = "/curso-notas-explicativas.html";
  const SOURCES = { conversao: "ofxs|conversao|dcne", email: "ofxs|email|dcne", pagina: "ofxs|pagina|dcne" };
  const session = window.OfxSession;
  const seenKey = "ofx_course_cta_seen_v2";
  let seen = new Set();
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(seenKey) || "[]");
    if (Array.isArray(stored)) seen = new Set(stored.slice(-100));
  } catch (_error) { /* Tracking remains optional when browser storage is unavailable. */ }
  let identityPromise;
  let revision = 0;
  let panel;
  let impressionId;
  let observer;
  let telemetryQueue = Promise.resolve();

  function recordEvent(eventType, pagePath) {
    // Keep exposure/visit before its click even on a slow connection.
    telemetryQueue = telemetryQueue.then(function () { return sendEvent(eventType, pagePath); });
    return telemetryQueue;
  }

  async function sendEvent(eventType, pagePath) {
    try {
      if (!session) return;
      if (!identityPromise) {
        identityPromise = (async function () {
          await session.ready();
          if (await session.getCurrentUser()) return true;
          const response = await fetch(`${session.apiBase}/auth/anonymous-session`, {
            method: "POST", credentials: "include", headers: { "content-type": "application/json" }, body: "{}",
          });
          return response.ok;
        })();
      }
      if (!(await identityPromise)) { identityPromise = null; return; }
      await fetch(`${session.apiBase}/telemetry/events`, {
        method: "POST", credentials: "include", keepalive: true,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ event_type: eventType, page_path: pagePath }),
      });
    } catch (_error) {
      identityPromise = null;
      // Never block a download or navigation when telemetry is unavailable.
    }
  }

  function trackLink(link, eventType, pagePath, beforeClick) {
    function record() {
      if (beforeClick) beforeClick();
      void recordEvent(eventType, pagePath);
    }
    link.addEventListener("click", record);
    link.addEventListener("auxclick", function (event) {
      if (event.button === 1) record();
    });
  }

  function hide() {
    revision += 1;
    impressionId = null;
    if (observer) observer.disconnect();
    if (panel) panel.classList.add("hidden");
  }

  function recordVisibleImpression() {
    if (!impressionId || !panel || panel.classList.contains("hidden") || document.visibilityState !== "visible") return;
    const rect = panel.getBoundingClientRect();
    const visibleWidth = Math.max(0, Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0));
    const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
    if (!rect.width || !rect.height || visibleWidth * visibleHeight < rect.width * rect.height * 0.5) return;
    const processingId = impressionId;
    impressionId = null;
    if (observer) observer.disconnect();
    if (seen.has(processingId)) return;
    seen.add(processingId);
    seen = new Set(Array.from(seen).slice(-100));
    try { window.sessionStorage.setItem(seenKey, JSON.stringify(Array.from(seen))); } catch (_error) { /* Optional. */ }
    void recordEvent("course_cta_view", window.location.pathname);
  }

  document.addEventListener("visibilitychange", recordVisibleImpression);
  window.addEventListener("scroll", recordVisibleImpression, { passive: true });
  window.addEventListener("resize", recordVisibleImpression, { passive: true });

  async function show(processingId, quotaMode) {
    hide();
    if (!ENABLED || !session || !processingId || quotaMode !== "conversion") return false;
    const expectedRevision = revision;
    let user;
    try { user = await session.getCurrentUser(); }
    catch (_error) { return true; }
    if (revision !== expectedRevision) return true;
    // The current entitlement and the completed conversion must both be free.
    if (user && (user.quota_mode !== "conversion" || (user.plan_code && user.plan_code !== "free"))) return false;
    const container = document.getElementById("download-section");
    if (!container) return false;
    if (!panel) {
      panel = document.createElement("aside");
      panel.id = "course-promotion";
      panel.className = "course-promotion hidden";
      panel.setAttribute("aria-labelledby", "course-promotion-title");
      const title = document.createElement("h3");
      title.id = "course-promotion-title";
      title.textContent = "Quer aprender a elaborar demonstrações contábeis?";
      const link = document.createElement("a");
      link.className = "btn btn-secondary";
      link.href = `${COURSE_PATH}?from=conversao`;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Saiba como";
      trackLink(link, "course_cta_click", window.location.pathname, recordVisibleImpression);
      const label = document.createElement("small");
      label.textContent = "Parceria";
      panel.append(title, link, label);
      container.appendChild(panel);
    }
    panel.classList.remove("hidden");
    if (!seen.has(processingId)) {
      impressionId = processingId;
      if (typeof IntersectionObserver === "function") {
        if (!observer) observer = new IntersectionObserver(recordVisibleImpression, { threshold: 0.5 });
        observer.observe(panel);
      }
      recordVisibleImpression();
    }
    return true;
  }

  window.OfxCoursePromotion = { show, hide };

  const offer = document.getElementById("course-offer-link");
  if (offer) {
    const requestedSource = new URLSearchParams(window.location.search).get("from");
    const source = Object.prototype.hasOwnProperty.call(SOURCES, requestedSource) ? requestedSource : "pagina";
    const hotlink = new URL(offer.href);
    hotlink.searchParams.set("src", SOURCES[source]);
    offer.href = hotlink.href;
    const pagePath = `${COURSE_PATH}?from=${source}`;
    void recordEvent("course_page_view", pagePath);
    trackLink(offer, "course_outbound_click", pagePath);
  }
})();
