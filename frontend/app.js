const DEFAULT_API_BASE = "http://127.0.0.1:8000";

const form = document.getElementById("analyze-form");
const bankFileInput = document.getElementById("bank-file-input");
const sheetFileInput = document.getElementById("sheet-file-input");
const apiBaseInput = document.getElementById("api-base");
const submitBtn = document.getElementById("submit-btn");
const errorNode = document.getElementById("error");
const uploadSuccessNode = document.getElementById("upload-success");
const resultNode = document.getElementById("result");
const statsNode = document.getElementById("stats");
const expiresInfoNode = document.getElementById("expires-info");
const resultExpiresInfoNode = document.getElementById("result-expires-info");
const apiStatus = document.getElementById("api-status");

function normalizeApiBase(value) {
  return (value || DEFAULT_API_BASE).replace(/\/+$/, "");
}

function formatExpiresAt(expiresAt) {
  if (!expiresAt) {
    return "";
  }

  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short"
  }).format(date);
}

function updateTrustMessage(expiresAt) {
  const formattedExpiresAt = formatExpiresAt(expiresAt);
  const message = formattedExpiresAt
    ? `Processamento temporario: esta analise expira em ${formattedExpiresAt}.`
    : "Processamento temporario: suas analises expiram automaticamente.";
  expiresInfoNode.textContent = message;
  resultExpiresInfoNode.textContent = message;
}

function setError(message) {
  if (!message) {
    errorNode.hidden = true;
    errorNode.textContent = "";
    return;
  }
  errorNode.hidden = false;
  errorNode.textContent = message;
}

function setSuccess(message) {
  if (!message) {
    uploadSuccessNode.hidden = true;
    uploadSuccessNode.textContent = "";
    return;
  }
  uploadSuccessNode.hidden = false;
  uploadSuccessNode.textContent = message;
}

function renderStats(data) {
  const metrics = [
    ["Status", String(data.status || "-")],
    ["Extrato", `${data.bank_filename || "-"} (${(data.bank_file_type || "-").toUpperCase()})`],
    ["Planilha", `${data.sheet_filename || "-"} (${(data.sheet_file_type || "-").toUpperCase()})`]
  ];

  statsNode.innerHTML = "";
  for (const [label, value] of metrics) {
    const item = document.createElement("div");
    item.className = "metric";
    const strong = document.createElement("strong");
    strong.textContent = label;
    const text = document.createElement("p");
    text.textContent = value;
    text.className = "muted";
    text.style.margin = "6px 0 0";
    item.append(strong, text);
    statsNode.appendChild(item);
  }
}

async function checkApi() {
  const baseUrl = normalizeApiBase(apiBaseInput.value);
  try {
    const response = await fetch(`${baseUrl}/health`);
    if (!response.ok) {
      throw new Error(`Status ${response.status}`);
    }
    apiStatus.textContent = "API online";
  } catch (_error) {
    apiStatus.textContent = "API offline. Inicie o backend em http://127.0.0.1:8000";
  }
}

apiBaseInput.addEventListener("change", () => {
  const baseUrl = normalizeApiBase(apiBaseInput.value);
  localStorage.setItem("gettdone_api_base", baseUrl);
  apiBaseInput.value = baseUrl;
  checkApi();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setError("");
  setSuccess("");
  resultNode.hidden = true;

  if (!bankFileInput.files || !bankFileInput.files[0]) {
    setError("Selecione um arquivo de extrato (CSV, XLSX ou OFX).");
    return;
  }

  if (!sheetFileInput.files || !sheetFileInput.files[0]) {
    setError("Selecione um arquivo de planilha (CSV ou XLSX).");
    return;
  }

  const bankFile = bankFileInput.files[0];
  const sheetFile = sheetFileInput.files[0];
  const baseUrl = normalizeApiBase(apiBaseInput.value);
  const formData = new FormData();
  formData.append("bank_file", bankFile);
  formData.append("sheet_file", sheetFile);
  updateTrustMessage("");

  submitBtn.disabled = true;
  submitBtn.textContent = "Enviando...";

  try {
    const response = await fetch(`${baseUrl}/reconcile`, {
      method: "POST",
      body: formData
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Falha ao enviar os arquivos.");
    }

    renderStats(payload);
    setSuccess("Upload aceito com sucesso. Proxima etapa: parsing e matching.");
    resultNode.hidden = false;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Erro inesperado.";
    setError(message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Enviar para conciliacao";
  }
});

(function init() {
  const savedBase = localStorage.getItem("gettdone_api_base");
  apiBaseInput.value = normalizeApiBase(savedBase || DEFAULT_API_BASE);
  updateTrustMessage("");
  checkApi();
})();
