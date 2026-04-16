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

  const reviewSection = document.getElementById("review-section");
  const downloadSection = document.getElementById("download-section");
  const reviewRows = document.getElementById("review-rows");
  const kpis = document.getElementById("kpis");

  const analysisIdNode = document.getElementById("analysis-id");
  const processingIdNode = document.getElementById("processing-id");
  const quotaRemainingNode = document.getElementById("quota-remaining");
  const downloadOfxBtn = document.getElementById("download-ofx-btn");
  const downloadExcelBtn = document.getElementById("download-excel-btn");
  const downloadCsvBtn = document.getElementById("download-csv-btn");
  const VIEW_STATE_KEY = "gettdone_ofx_convert_view_state_v1";
  const VIEW_STATE_TTL_MS = 24 * 60 * 60 * 1000;

  const state = {
    analysisId: null,
    processingId: null,
    isLoading: false,
    restoredFileMeta: null,
  };

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

  function getAnonymousFingerprint() {
    const key = "gettdone_anon_fingerprint";
    const existing = localStorage.getItem(key);
    if (existing) {
      return existing;
    }
    const generated = `anon-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    localStorage.setItem(key, generated);
    return generated;
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

  function isPdfFile(file) {
    if (!file) {
      return false;
    }
    const name = String(file.name || "").toLowerCase();
    const type = String(file.type || "").toLowerCase();
    return name.endsWith(".pdf") || type === "application/pdf";
  }

  function setStatus(message, kind) {
    statusMsg.textContent = message || "";
    statusMsg.classList.remove("error", "success");
    if (kind) {
      statusMsg.classList.add(kind);
    }
  }

  function saveViewState(payload) {
    try {
      localStorage.setItem(
        VIEW_STATE_KEY,
        JSON.stringify({
          saved_at: Date.now(),
          processing_id: payload.processing_id || null,
          analysis_id: payload.analysis_id || null,
          analysis: payload.analysis || null,
          quota_text: payload.quota_text || "-",
          file_name: payload.file_name || null,
          file_size: payload.file_size || null,
        }),
      );
    } catch (_error) {
      // Best-effort only: UI still works without persistence.
    }
  }

  function loadViewState() {
    try {
      const raw = localStorage.getItem(VIEW_STATE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      const savedAt = Number(parsed.saved_at || 0);
      if (!savedAt || Date.now() - savedAt > VIEW_STATE_TTL_MS) {
        localStorage.removeItem(VIEW_STATE_KEY);
        return null;
      }
      return parsed;
    } catch (_error) {
      localStorage.removeItem(VIEW_STATE_KEY);
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

  function setLoading(isLoading) {
    state.isLoading = isLoading;
    convertBtn.disabled = isLoading || !input.files || input.files.length === 0;
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
    convertBtn.disabled = !file || state.isLoading;
    if (dropzoneEmpty && dropzoneLoaded && dropzoneFileMeta) {
      if (file) {
        state.restoredFileMeta = null;
        dropzone.classList.add("is-filled");
        dropzoneEmpty.classList.add("hidden");
        dropzoneLoaded.classList.remove("hidden");
        dropzoneFileMeta.textContent = `${file.name} • ${formatFileSize(file.size)}`;
      } else if (hasRestoredMeta) {
        dropzone.classList.add("is-filled");
        dropzoneEmpty.classList.add("hidden");
        dropzoneLoaded.classList.remove("hidden");
        dropzoneFileMeta.textContent = `${restoredMeta.name} • ${formatFileSize(restoredMeta.size)}`;
      } else {
        dropzone.classList.remove("is-filled");
        dropzoneEmpty.classList.remove("hidden");
        dropzoneLoaded.classList.add("hidden");
        dropzoneFileMeta.textContent = "Pronto para conversão";
      }
    }
  }

  function clearSelectedFile() {
    input.value = "";
    state.restoredFileMeta = null;
    setSelectedFileLabel();
    clearViewState();
    setStatus("Arquivo removido. Selecione outro PDF para continuar.", null);
  }

  function renderKpis(analysis) {
    const entries = [
      ["Transações", analysis.transactions_total],
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

  function renderRows(rows) {
    if (!rows || rows.length === 0) {
      reviewRows.innerHTML = '<tr><td colspan="4">Nenhuma transação para exibir.</td></tr>';
      return;
    }

    reviewRows.innerHTML = rows
      .map(
        (row) => `
          <tr>
            <td>${formatDate(row.date)}</td>
            <td>${row.description || "-"}</td>
            <td>${Number(row.amount || 0) > 0 ? formatCurrency(row.amount) : "-"}</td>
            <td>${Number(row.amount || 0) < 0 ? formatCurrency(Math.abs(Number(row.amount || 0))) : "-"}</td>
          </tr>
        `,
      )
      .join("");
  }

  function restoreViewFromState(viewState) {
    const analysis = viewState.analysis;
    if (!analysis || !analysis.analysis_id) {
      return;
    }

    state.analysisId = viewState.analysis_id || analysis.analysis_id;
    state.processingId = viewState.processing_id || analysis.analysis_id;
    state.restoredFileMeta = {
      name: String(viewState.file_name || "").trim() || "arquivo_restaurado.pdf",
      size: Number(viewState.file_size || 0),
    };

    renderKpis(analysis);
    renderRows(analysis.preview_transactions || []);
    setSelectedFileLabel();

    analysisIdNode.textContent = state.analysisId || "-";
    processingIdNode.textContent = state.processingId || "-";
    quotaRemainingNode.textContent = viewState.quota_text || "-";

    reviewSection.classList.remove("hidden");
    downloadSection.classList.remove("hidden");

    const canDownload = Boolean(state.analysisId || state.processingId);
    if (downloadOfxBtn) downloadOfxBtn.disabled = !canDownload;
    if (downloadExcelBtn) downloadExcelBtn.disabled = !canDownload;
    if (downloadCsvBtn) downloadCsvBtn.disabled = !canDownload;

    setStatus("Sessão restaurada. Você pode continuar o download.", "success");
  }

  async function postConvert(formData) {
    const response = await fetch(`${apiBase}/convert`, {
      method: "POST",
      body: formData,
    });

    const payload = await response.json().catch(() => ({}));

    if (response.status === 404 || response.status === 405) {
      return null;
    }

    if (!response.ok) {
      const detail = payload.detail || "Falha ao converter arquivo.";
      throw new Error(detail);
    }

    return payload;
  }

  async function postAnalyze(file) {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(`${apiBase}/analyze`, {
      method: "POST",
      body: formData,
    });

      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
      const detail = payload.detail || "Falha ao processar arquivo via /analyze.";
      throw new Error(detail);
    }

    return {
      processing_id: payload.analysis_id,
      quota_remaining: null,
      quota_limit: null,
      analysis: payload,
      mode: "analyze",
    };
  }

  async function runConvert() {
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
      formData.append("anonymous_fingerprint", getAnonymousFingerprint());

      let payload = await postConvert(formData);
      if (!payload) {
        payload = await postAnalyze(file);
      }

      const analysis = payload.analysis;
      state.analysisId = analysis.analysis_id;
      state.processingId = payload.processing_id || analysis.analysis_id;

      renderKpis(analysis);
      renderRows(analysis.preview_transactions || []);

      analysisIdNode.textContent = analysis.analysis_id || "-";
      processingIdNode.textContent = state.processingId || "-";
      if (payload.quota_remaining === null || payload.quota_limit === null) {
        quotaRemainingNode.textContent = "n/d (modo analyze)";
      } else {
        quotaRemainingNode.textContent = `${payload.quota_remaining} / ${payload.quota_limit}`;
      }

      reviewSection.classList.remove("hidden");
      downloadSection.classList.remove("hidden");
      const canDownload = Boolean(state.analysisId);
      if (downloadOfxBtn) downloadOfxBtn.disabled = !canDownload;
      if (downloadExcelBtn) downloadExcelBtn.disabled = !canDownload;
      if (downloadCsvBtn) downloadCsvBtn.disabled = !canDownload;

      saveViewState({
        processing_id: state.processingId,
        analysis_id: state.analysisId,
        analysis,
        quota_text: quotaRemainingNode.textContent || "-",
        file_name: file.name || null,
        file_size: Number(file.size || 0),
      });

      if (payload.mode === "analyze") {
        setStatus("Conversão concluída via /analyze. Revise os dados e baixe o relatório.", "success");
      } else {
        setStatus("Conversão concluída. Revise os dados e baixe o relatório.", "success");
      }
      reviewSection.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Erro inesperado.", "error");
    } finally {
      setLoading(false);
    }
  }

  function runDownloadExcel() {
    if (!state.analysisId) {
      setStatus("Converta um arquivo antes de baixar.", "error");
      return;
    }
    window.open(`${apiBase}/report/${state.analysisId}`, "_blank", "noopener");
  }

  function runDownloadOfx() {
    if (!state.processingId) {
      setStatus("Converta um arquivo antes de baixar.", "error");
      return;
    }
    window.open(`${apiBase}/convert-report/${state.processingId}?format=ofx`, "_blank", "noopener");
  }

  function runDownloadCsv() {
    if (!state.processingId) {
      setStatus("Converta um arquivo antes de baixar.", "error");
      return;
    }
    window.open(`${apiBase}/convert-report/${state.processingId}?format=csv`, "_blank", "noopener");
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
  convertBtn.addEventListener("click", runConvert);
  if (downloadOfxBtn) downloadOfxBtn.addEventListener("click", runDownloadOfx);
  if (downloadExcelBtn) downloadExcelBtn.addEventListener("click", runDownloadExcel);
  if (downloadCsvBtn) downloadCsvBtn.addEventListener("click", runDownloadCsv);
  if (clearFileBtn) {
    clearFileBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      clearSelectedFile();
    });
  }

  bindDropzone();
  setSelectedFileLabel();

  const persistedState = loadViewState();
  if (persistedState) {
    restoreViewFromState(persistedState);
  }
})();
