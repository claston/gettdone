(function () {
  "use strict";
  const data = window.PURCHASE_EMAIL_PREVIEW;
  const frame = document.getElementById("email-frame");
  const text = document.getElementById("text-content");
  const feedback = document.getElementById("preview-feedback");
  const copySection = document.getElementById("copy-section");
  document.querySelectorAll("[data-support-number]").forEach((node) => {
    node.textContent = data.sample.whatsapp_display;
  });
  let current = 0;
  let plainText = false;
  const escape = (value) => String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
  function renderFields(source, html = false) {
    return source.replace(/\{\{\s*([a-z_]+)\s*\}\}/g, (match, key) => {
      if (!(key in data.sample)) throw new Error(`Missing preview field: ${key}`);
      return html ? escape(data.sample[key]) : data.sample[key];
    });
  }
  function resizeFrame() {
    if (frame.contentDocument?.body) {
      frame.style.height = `${frame.contentDocument.body.scrollHeight + 4}px`;
    }
  }
  function showCopyPage() {
    copySection.hidden = false;
    document.getElementById("payment-link").value = data.sample.payment_url;
    copySection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function selectMessage(index) {
    current = index;
    const message = data.messages[current];
    document.querySelectorAll(".message-button").forEach((button, ordinal) => {
      button.setAttribute("aria-pressed", String(ordinal === index));
    });
    document.getElementById("recipient").textContent = message.audience === "Administrador" ? "Equipe administrativa" : data.sample.customer_email;
    document.getElementById("subject").textContent = renderFields(message.subject);
    document.getElementById("preheader").textContent = message.preheader;
    document.getElementById("message-note").textContent = message.note;
    document.getElementById("source").href = `../../../backend/app/email_templates/purchase/${message.id}.html`;
    frame.srcdoc = renderFields(message.html, true);
    text.textContent = renderFields(message.text);
    feedback.textContent = "";
    copySection.hidden = true;
  }
  data.messages.forEach((message, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "message-button";
    button.innerHTML = `<span class="ordinal">${["01", "02", "03a", "03b", "Adm"][index]}</span><span><strong>${escape(message.label)}</strong><small>${escape(message.audience)}</small></span>`;
    button.addEventListener("click", () => selectMessage(index));
    document.getElementById("messages").appendChild(button);
  });
  frame.addEventListener("load", () => {
    resizeFrame();
    frame.contentDocument.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link) return;
      event.preventDefault();
      if (link.getAttribute("href") === data.sample.payment_page_url) {
        showCopyPage();
      } else {
        feedback.textContent = `Prévia de “${link.textContent.trim()}”: nenhuma ação externa foi executada.`;
      }
    });
  });
  window.addEventListener("resize", resizeFrame);
  ["desktop", "mobile"].forEach((mode) => {
    document.getElementById(mode).addEventListener("click", () => {
      frame.classList.toggle("mobile", mode === "mobile");
      document.getElementById("desktop").setAttribute("aria-pressed", String(mode === "desktop"));
      document.getElementById("mobile").setAttribute("aria-pressed", String(mode === "mobile"));
      resizeFrame();
    });
  });
  ["html", "text"].forEach((mode) => {
    document.getElementById(`${mode}-mode`).addEventListener("click", () => {
      plainText = mode === "text";
      frame.hidden = plainText;
      text.hidden = !plainText;
      document.getElementById("html-mode").setAttribute("aria-pressed", String(!plainText));
      document.getElementById("text-mode").setAttribute("aria-pressed", String(plainText));
      if (!plainText) resizeFrame();
    });
  });
  document.getElementById("close-copy").addEventListener("click", () => { copySection.hidden = true; });
  document.getElementById("demo-payment").addEventListener("click", () => {
    document.getElementById("copy-status").textContent = "Esta é uma prévia. Nenhum pagamento foi iniciado.";
  });
  document.getElementById("copy-link").addEventListener("click", async () => {
    const status = document.getElementById("copy-status");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(data.sample.payment_url);
      status.textContent = "Link copiado! Cole onde preferir.";
    } catch (_error) {
      const input = document.getElementById("payment-link");
      input.focus();
      input.select();
      status.textContent = "Selecione e copie o link acima. Seu navegador não permitiu a cópia automática.";
    }
  });
  selectMessage(0);
})();
