(function () {
  const STORAGE_KEY = 'ofx_privacy_preferences_v1';
  const POLICY_VERSION = '2026-05-15';
  const GTM_ID = 'GTM-KXD6TMPC';

  function readPrefs() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      return parsed;
    } catch (_) {
      return null;
    }
  }

  function savePrefs(next) {
    const payload = {
      necessary: true,
      analytics: !!next.analytics,
      marketing: !!next.marketing,
      policyVersion: POLICY_VERSION,
      timestamp: new Date().toISOString()
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    return payload;
  }

  function shouldLoadGtm(prefs) {
    return !!(prefs && (prefs.analytics || prefs.marketing));
  }

  function loadGtm() {
    if (window.__ofxGtmLoaded) return;
    window.__ofxGtmLoaded = true;
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({ 'gtm.start': new Date().getTime(), event: 'gtm.js' });

    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://www.googletagmanager.com/gtm.js?id=' + GTM_ID;
    document.head.appendChild(script);
  }

  function applyPrefs(prefs) {
    if (shouldLoadGtm(prefs)) {
      loadGtm();
    }
  }

  function createPreferencesPanel(currentPrefs) {
    const panel = document.createElement('div');
    panel.id = 'privacy-preferences-panel';
    panel.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:99999;padding:16px;';

    const card = document.createElement('div');
    card.style.cssText = 'width:min(560px,100%);background:#fff;border-radius:12px;padding:20px;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111827;';
    card.innerHTML =
      '<h2 style="margin:0 0 8px;font-size:20px;">Preferências de privacidade</h2>' +
      '<p style="margin:0 0 16px;font-size:14px;line-height:1.5;">Você pode escolher quais cookies e tecnologias opcionais deseja permitir.</p>' +
      '<label style="display:flex;gap:8px;align-items:flex-start;margin:0 0 10px;font-size:14px;"><input type="checkbox" checked disabled><span><strong>Necessários</strong><br>Essenciais para funcionamento do site.</span></label>' +
      '<label style="display:flex;gap:8px;align-items:flex-start;margin:0 0 10px;font-size:14px;"><input id="privacy-analytics" type="checkbox"><span><strong>Analytics</strong><br>Medição de uso para melhorar o produto.</span></label>' +
      '<label style="display:flex;gap:8px;align-items:flex-start;margin:0 0 16px;font-size:14px;"><input id="privacy-marketing" type="checkbox"><span><strong>Marketing</strong><br>Mensuração de campanhas.</span></label>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      '<button id="privacy-save" style="border:0;background:#111827;color:#fff;padding:10px 14px;border-radius:8px;cursor:pointer;">Salvar preferências</button>' +
      '<button id="privacy-reject" style="border:1px solid #d1d5db;background:#fff;color:#111827;padding:10px 14px;border-radius:8px;cursor:pointer;">Recusar opcionais</button>' +
      '</div>';

    panel.appendChild(card);
    document.body.appendChild(panel);

    const analyticsInput = card.querySelector('#privacy-analytics');
    const marketingInput = card.querySelector('#privacy-marketing');
    analyticsInput.checked = !!(currentPrefs && currentPrefs.analytics);
    marketingInput.checked = !!(currentPrefs && currentPrefs.marketing);

    const close = function () {
      if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
    };

    card.querySelector('#privacy-save').addEventListener('click', function () {
      const prefs = savePrefs({ analytics: analyticsInput.checked, marketing: marketingInput.checked });
      applyPrefs(prefs);
      close();
    });

    card.querySelector('#privacy-reject').addEventListener('click', function () {
      savePrefs({ analytics: false, marketing: false });
      close();
    });
  }

  function createBanner() {
    const banner = document.createElement('div');
    banner.id = 'privacy-consent-banner';
    banner.style.cssText = 'position:fixed;left:16px;right:16px;bottom:16px;z-index:99998;background:#111827;color:#fff;border-radius:12px;padding:14px;box-shadow:0 10px 24px rgba(0,0,0,.25);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;';
    banner.innerHTML =
      '<p style="margin:0 0 10px;font-size:13px;line-height:1.45;">Usamos cookies e tecnologias similares para funcionamento do site e, com sua permissão, para analytics e marketing.</p>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;">' +
      '<button id="privacy-accept" style="border:0;background:#22c55e;color:#052e16;padding:9px 12px;border-radius:8px;cursor:pointer;font-weight:600;">Aceitar opcionais</button>' +
      '<button id="privacy-deny" style="border:1px solid #4b5563;background:#111827;color:#fff;padding:9px 12px;border-radius:8px;cursor:pointer;">Recusar opcionais</button>' +
      '<button id="privacy-customize" style="border:1px solid #4b5563;background:transparent;color:#fff;padding:9px 12px;border-radius:8px;cursor:pointer;">Personalizar</button>' +
      '</div>';

    document.body.appendChild(banner);

    const remove = function () {
      if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
    };

    banner.querySelector('#privacy-accept').addEventListener('click', function () {
      const prefs = savePrefs({ analytics: true, marketing: true });
      applyPrefs(prefs);
      remove();
    });

    banner.querySelector('#privacy-deny').addEventListener('click', function () {
      savePrefs({ analytics: false, marketing: false });
      remove();
    });

    banner.querySelector('#privacy-customize').addEventListener('click', function () {
      remove();
      createPreferencesPanel(readPrefs());
    });
  }

  function ensurePreferencesEntryPoint() {
    if (document.getElementById('privacy-preferences-link')) return;
    const button = document.createElement('button');
    button.id = 'privacy-preferences-link';
    button.type = 'button';
    button.textContent = 'Preferências de privacidade';
    button.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:99997;border:1px solid #d1d5db;background:#fff;color:#111827;padding:8px 10px;border-radius:999px;font-size:12px;cursor:pointer;';
    button.addEventListener('click', function () {
      createPreferencesPanel(readPrefs() || { analytics: false, marketing: false });
    });
    document.body.appendChild(button);
  }

  function init() {
    const prefs = readPrefs();
    applyPrefs(prefs);
    ensurePreferencesEntryPoint();
    if (!prefs) {
      createBanner();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
