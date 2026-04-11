const DEFAULT_API_BASE = "http://127.0.0.1:8000";

const form = document.getElementById("analyze-form");
const bankFileInput = document.getElementById("bank-file-input");
const sheetFileInput = document.getElementById("sheet-file-input");
const apiBaseInput = document.getElementById("api-base");
const analyzeBtn = document.getElementById("analyze-btn");
const submitBtn = document.getElementById("submit-btn");
const errorNode = document.getElementById("error");
const uploadSuccessNode = document.getElementById("upload-success");
const resultNode = document.getElementById("result");
const resultTitle = document.getElementById("result-title");
const statsNode = document.getElementById("stats");
const analyzePreviewNode = document.getElementById("analyze-preview");
const reconcilePreviewNode = document.getElementById("reconcile-preview");
const reconcileHeadlineNode = document.getElementById("reconcile-headline");
const reconcileRowsBody = document.getElementById("reconcile-rows-body");
const beforeAfterWrap = document.getElementById("before-after-wrap");
const beforeAfterBody = document.getElementById("before-after-body");
const previewBody = document.getElementById("preview-body");
const downloadLink = document.getElementById("download-link");
const expiresInfoNode = document.getElementById("expires-info");
const resultExpiresInfoNode = document.getElementById("result-expires-info");
const apiStatus = document.getElementById("api-status");

function normalizeApiBase(value) {
  return (value || DEFAULT_API_BASE).replace(/\/+$/, "");
}

function formatCurrency(value) {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL"
  }).format(Number(value || 0));
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

function setLoading(isLoading, submitText, analyzeText) {
  submitBtn.disabled = isLoading;
  analyzeBtn.disabled = isLoading;
  submitBtn.textContent = submitText;
  analyzeBtn.textContent = analyzeText;
}

function renderStats(metrics) {
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

function renderAnalyzeStats(data) {
  const operational = data.operational_summary || {};
  renderStats([
    ["Transacoes", String(data.transactions_total || 0)],
    ["Entradas", formatCurrency(data.total_inflows)],
    ["Saidas", formatCurrency(data.total_outflows)],
    ["Saldo", formatCurrency(data.net_total)],
    ["Volume Total", formatCurrency(operational.total_volume || 0)],
    ["Qtd Entradas", String(operational.inflow_count || 0)],
    ["Qtd Saidas", String(operational.outflow_count || 0)]
  ]);
}

function renderReconcileStats(data) {
  renderStats([
    ["Status", String(data.status || "-")],
    ["Extrato", `${data.bank_filename || "-"} (${(data.bank_file_type || "-").toUpperCase()})`],
    ["Planilha", `${data.sheet_filename || "-"} (${(data.sheet_file_type || "-").toUpperCase()})`],
    ["Conciliados", String(data.conciliated_count || 0)],
    ["Pendentes", String(data.pending_count || 0)],
    ["Divergentes", String(data.divergent_count || 0)],
    ["Pendentes na planilha", String(data.bank_unmatched_count || 0)],
    ["Pendentes no banco", String(data.sheet_unmatched_count || 0)]
  ]);
}

function reconcileReasonLabel(reason) {
  const labels = {
    missing_in_sheet: "Pendente na planilha",
    missing_in_bank: "Pendente no banco",
    amount_mismatch: "Valor divergente",
    date_out_of_tolerance_window: "Data fora da tolerancia"
  };
  return labels[reason] || String(reason || "-");
}

function reconcileStatusLabel(status) {
  const labels = {
    conciliado: "Conciliado",
    pendente: "Pendente",
    divergente: "Divergente"
  };
  return labels[status] || String(status || "-");
}

function reconcileSourceLabel(source) {
  return source === "bank" ? "Extrato" : "Planilha";
}

function renderReconcileRows(rows) {
  reconcileRowsBody.innerHTML = "";
  const actionableRows = (rows || []).filter((row) => row.status !== "conciliado");

  if (actionableRows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "Sem pendencias ou divergencias neste arquivo.";
    tr.appendChild(td);
    reconcileRowsBody.appendChild(tr);
    return;
  }

  for (const row of actionableRows) {
    const tr = document.createElement("tr");
    const values = [
      reconcileSourceLabel(row.source),
      row.date,
      row.description,
      formatCurrency(row.amount),
      reconcileStatusLabel(row.status),
      reconcileReasonLabel(row.reason),
      row.matched_row_id || "-"
    ];

    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = String(value || "");
      tr.appendChild(td);
    }

    reconcileRowsBody.appendChild(tr);
  }
}

function renderReconcilePreview(data) {
  const pending = Number(data.pending_count || 0);
  const divergent = Number(data.divergent_count || 0);
  const bankPending = Number(data.bank_unmatched_count || 0);
  const sheetPending = Number(data.sheet_unmatched_count || 0);
  reconcileHeadlineNode.textContent =
    `${pending} pendentes e ${divergent} divergentes. ` +
    `${bankPending} pendentes na planilha e ${sheetPending} pendentes no banco.`;
  renderReconcileRows(data.reconciliation_rows || []);
  reconcilePreviewNode.hidden = false;
}

function renderPreviewRows(rows) {
  previewBody.innerHTML = "";
  for (const row of rows || []) {
    const tr = document.createElement("tr");
    const values = [
      row.date,
      row.description,
      formatCurrency(row.amount),
      row.category,
      row.reconciliation_status
    ];

    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = String(value || "");
      tr.appendChild(td);
    }

    previewBody.appendChild(tr);
  }
}

function renderBeforeAfterRows(rows) {
  beforeAfterBody.innerHTML = "";
  const hasRows = Array.isArray(rows) && rows.length > 0;
  beforeAfterWrap.hidden = !hasRows;
  if (!hasRows) {
    return;
  }

  for (const row of rows) {
    const tr = document.createElement("tr");
    const values = [
      row.date,
      row.description_before,
      row.description_after,
      formatCurrency(row.amount_before),
      formatCurrency(row.amount_after)
    ];

    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = String(value || "");
      tr.appendChild(td);
    }

    beforeAfterBody.appendChild(tr);
  }
}

async function parseJsonSafe(response) {
  try {
    return await response.json();
  } catch (_error) {
    return {};
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

async function runAnalyze() {
  setError("");
  setSuccess("");
  resultNode.hidden = true;

  if (!bankFileInput.files || !bankFileInput.files[0]) {
    setError("Selecione um arquivo de extrato (CSV, XLSX ou OFX).");
    return;
  }

  const baseUrl = normalizeApiBase(apiBaseInput.value);
  const formData = new FormData();
  formData.append("file", bankFileInput.files[0]);

  setLoading(true, "Enviar para conciliacao", "Analisando...");

  try {
    const response = await fetch(`${baseUrl}/analyze`, {
      method: "POST",
      body: formData
    });

    const payload = await parseJsonSafe(response);
    if (!response.ok) {
      throw new Error(payload.detail || "Falha ao analisar extrato.");
    }

    resultTitle.textContent = "Preview da analise";
    renderAnalyzeStats(payload);
    renderPreviewRows(payload.preview_transactions || []);
    renderBeforeAfterRows(payload.preview_before_after || []);
    analyzePreviewNode.hidden = false;
    reconcilePreviewNode.hidden = true;
    updateTrustMessage(payload.expires_at);
    downloadLink.href = `${baseUrl}/report/${payload.analysis_id}`;
    resultNode.hidden = false;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Erro inesperado.";
    setError(message);
  } finally {
    setLoading(false, "Enviar para conciliacao", "Analisar extrato (preview)");
  }
}

async function runReconcile() {
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

  const baseUrl = normalizeApiBase(apiBaseInput.value);
  const formData = new FormData();
  formData.append("bank_file", bankFileInput.files[0]);
  formData.append("sheet_file", sheetFileInput.files[0]);

  setLoading(true, "Enviando...", "Analisar extrato (preview)");

  try {
    const response = await fetch(`${baseUrl}/reconcile`, {
      method: "POST",
      body: formData
    });

    const payload = await parseJsonSafe(response);
    if (!response.ok) {
      throw new Error(payload.detail || "Falha ao enviar os arquivos.");
    }

    resultTitle.textContent = "Upload recebido";
    renderReconcileStats(payload);
    analyzePreviewNode.hidden = true;
    renderReconcilePreview(payload);
    updateTrustMessage("");
    setSuccess("Conciliacao concluida. Revise as pendencias e divergencias destacadas.");
    resultNode.hidden = false;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Erro inesperado.";
    setError(message);
  } finally {
    setLoading(false, "Enviar para conciliacao", "Analisar extrato (preview)");
  }
}

apiBaseInput.addEventListener("change", () => {
  const baseUrl = normalizeApiBase(apiBaseInput.value);
  localStorage.setItem("gettdone_api_base", baseUrl);
  apiBaseInput.value = baseUrl;
  checkApi();
});

analyzeBtn.addEventListener("click", () => {
  runAnalyze();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await runReconcile();
});

(function init() {
  const savedBase = localStorage.getItem("gettdone_api_base");
  apiBaseInput.value = normalizeApiBase(savedBase || DEFAULT_API_BASE);
  updateTrustMessage("");
  checkApi();
})();
