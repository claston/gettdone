function bindDropzone(config) {
        const dropzone = document.getElementById(config.dropzoneId);
        const fileInput = document.getElementById(config.inputId);
        const fileName = document.getElementById(config.fileNameId);

        if (!dropzone || !fileInput || !fileName) {
            return;
        }

        const setName = function () {
            if (fileInput.files && fileInput.files.length > 0) {
                fileName.textContent = fileInput.files[0].name;
                dropzone.classList.add("is-filled");
                dropzone.classList.remove("is-invalid");
            } else {
                fileName.textContent = config.emptyLabel;
                dropzone.classList.remove("is-filled");
            }
        };

        const assignFile = function (files) {
            if (!files || files.length === 0) {
                return;
            }

            const transfer = new DataTransfer();
            transfer.items.add(files[0]);
            fileInput.files = transfer.files;
            fileInput.dispatchEvent(new Event("change", { bubbles: true }));
        };

        dropzone.addEventListener("click", function () {
            fileInput.click();
        });

        dropzone.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                fileInput.click();
            }
        });

        dropzone.addEventListener("dragover", function (event) {
            event.preventDefault();
            dropzone.classList.add("is-dragover");
        });

        dropzone.addEventListener("dragenter", function (event) {
            event.preventDefault();
            dropzone.classList.add("is-dragover");
        });

        dropzone.addEventListener("dragleave", function (event) {
            if (!dropzone.contains(event.relatedTarget)) {
                dropzone.classList.remove("is-dragover");
            }
        });

        dropzone.addEventListener("drop", function (event) {
            event.preventDefault();
            dropzone.classList.remove("is-dragover");
            assignFile(event.dataTransfer.files);
        });

        fileInput.addEventListener("change", setName);
        setName();
    }

    bindDropzone({
        dropzoneId: "bank-dropzone",
        inputId: "bank-file-input",
        fileNameId: "bank-file-name",
        emptyLabel: "Nenhum arquivo selecionado"
    });

    bindDropzone({
        dropzoneId: "sheet-dropzone",
        inputId: "sheet-file-input",
        fileNameId: "sheet-file-name",
        emptyLabel: "Nenhum arquivo selecionado"
    });

    const DEFAULT_API_BASE = "http://127.0.0.1:8000";
    const showReportBtn = document.getElementById("show-report-btn");
    const topCtaStart = document.getElementById("top-cta-start");
    const uploadValidation = document.getElementById("upload-validation");
    const bankFileInput = document.getElementById("bank-file-input");
    const sheetFileInput = document.getElementById("sheet-file-input");
    const bankDropzone = document.getElementById("bank-dropzone");
    const sheetDropzone = document.getElementById("sheet-dropzone");
    const uploadSection = document.getElementById("upload-section");
    const topNav = document.getElementById("top-nav");
    const reconcileReportPreview = document.getElementById("reconcile-report-preview");
    const reconcileRowsBody = document.getElementById("reconcile-rows-body");
    const reconcileSearchInput = document.getElementById("reconcile-search-input");
    const reconcileSearchClear = document.getElementById("reconcile-search-clear");
    const reconcilePageInfo = document.getElementById("reconcile-page-info");
    const reconcilePrevPage = document.getElementById("reconcile-prev-page");
    const reconcileNextPage = document.getElementById("reconcile-next-page");
    const reconcilePageNumbers = document.getElementById("reconcile-page-numbers");
    const reconcileDownloadXlsx = document.getElementById("reconcile-download-xlsx");
    const reconcileDownloadCsv = document.getElementById("reconcile-download-csv");
    const metricConciliated = document.getElementById("metric-conciliated");
    const metricConciliatedRate = document.getElementById("metric-conciliated-rate");
    const metricPending = document.getElementById("metric-pending");
    const metricDivergent = document.getElementById("metric-divergent");
    const metricPendingTotal = document.getElementById("metric-pending-total");
    const metricBankRows = document.getElementById("metric-bank-rows");
    const metricSheetRows = document.getElementById("metric-sheet-rows");
    const metricMissingInSheet = document.getElementById("metric-missing-in-sheet");
    const metricMissingInBank = document.getElementById("metric-missing-in-bank");
    let reportFocusMode = false;
    let isSubmitting = false;
    const ROWS_PER_PAGE = 10;
    let allReconcileRows = [];
    let filteredReconcileRows = [];
    let reconcileCurrentPage = 1;

    function focusUploadSection() {
        if (!uploadSection) {
            return;
        }

        uploadSection.scrollIntoView({ behavior: "smooth", block: "start" });
        window.setTimeout(function () {
            if (bankDropzone) {
                bankDropzone.focus({ preventScroll: true });
            }
        }, 350);
    }

    function normalizeApiBase(value) {
        return (value || DEFAULT_API_BASE).replace(/\/+$/, "");
    }

    function getApiBase() {
        return normalizeApiBase(localStorage.getItem("gettdone_api_base") || DEFAULT_API_BASE);
    }

    function formatCurrency(value) {
        return new Intl.NumberFormat("pt-BR", {
            style: "currency",
            currency: "BRL"
        }).format(Number(value || 0));
    }

    function formatDate(value) {
        if (!value) {
            return "-";
        }
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) {
            return String(value);
        }
        return new Intl.DateTimeFormat("pt-BR").format(dt);
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    async function parseJsonSafe(response) {
        try {
            return await response.json();
        } catch (_error) {
            return {};
        }
    }

    function getFriendlyValidationMessage(detail) {
        const message = String(detail || "");
        const normalized = message.toLowerCase();

        if (normalized.includes("missing required columns")) {
            return "A planilha precisa ter as colunas de data, descricao e valor.";
        }

        if (normalized.includes("ambiguous column mapping")) {
            return "Nao consegui identificar com seguranca as colunas da planilha. Use nomes mais claros como Data, Descricao e Valor.";
        }

        return message || "Falha ao processar conciliacao.";
    }

    function isSheetColumnValidationError(detail) {
        const message = String(detail || "").toLowerCase();
        return message.includes("missing required columns") || message.includes("ambiguous column mapping");
    }

    function setSheetValidationErrorState(isInvalid) {
        if (!sheetDropzone) {
            return;
        }

        sheetDropzone.classList.toggle("is-invalid", Boolean(isInvalid));
    }

    function hasSelectedFile(input) {
        return Boolean(input && input.files && input.files.length > 0);
    }

    function setValidationMessage(message) {
        if (!uploadValidation) {
            return;
        }
        if (!message) {
            uploadValidation.hidden = true;
            uploadValidation.textContent = "";
            return;
        }
        uploadValidation.textContent = message;
        uploadValidation.hidden = false;
    }

    function updateReportGateState(options) {
        if (!showReportBtn) {
            return false;
        }

        const shouldClearMessage = !options || options.clearMessage !== false;
        const ready = hasSelectedFile(bankFileInput) && hasSelectedFile(sheetFileInput);
        showReportBtn.disabled = isSubmitting || !ready;
        showReportBtn.classList.toggle("opacity-70", !ready || isSubmitting);
        showReportBtn.classList.toggle("cursor-not-allowed", !ready || isSubmitting);
        showReportBtn.classList.toggle("pointer-events-none", !ready || isSubmitting);

        if (ready && shouldClearMessage) {
            setValidationMessage("");
        }

        return ready;
    }

    function showMissingFilesFeedback() {
        const missing = [];

        if (!hasSelectedFile(bankFileInput)) {
            missing.push("extrato bancario");
            if (bankDropzone) {
                bankDropzone.classList.add("is-invalid");
            }
        } else if (bankDropzone) {
            bankDropzone.classList.remove("is-invalid");
        }

        if (!hasSelectedFile(sheetFileInput)) {
            missing.push("planilha");
            if (sheetDropzone) {
                sheetDropzone.classList.add("is-invalid");
            }
        } else if (sheetDropzone) {
            sheetDropzone.classList.remove("is-invalid");
        }

        setValidationMessage(`Selecione ${missing.join(" e ")} para gerar o relatorio.`);
    }

    function setTopNavHidden(hidden) {
        if (!topNav) {
            return;
        }
        topNav.classList.toggle("is-hidden", hidden);
    }

    function isReportInViewport() {
        if (!reconcileReportPreview || reconcileReportPreview.classList.contains("hidden")) {
            return false;
        }

        const rect = reconcileReportPreview.getBoundingClientRect();
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
        return rect.top < viewportHeight && rect.bottom > 0;
    }

    function handleScrollNavFocus() {
        if (!reportFocusMode) {
            return;
        }
        setTopNavHidden(isReportInViewport());
    }

    function reasonLabel(reason) {
        const labels = {
            missing_in_sheet: "Pendente na planilha",
            missing_in_bank: "Pendente no banco",
            amount_mismatch: "Diferenca de valor",
            date_out_of_tolerance_window: "Data fora da tolerancia"
        };
        return labels[reason] || "-";
    }

    function sourceBadgeHtml(source) {
        if (source === "bank") {
            return '<span class="flex items-center gap-2 text-xs font-semibold text-on-surface-variant px-2 py-1 bg-surface-container rounded-md"><span class="material-symbols-outlined text-[14px]" data-icon="account_balance">account_balance</span> BANCO</span>';
        }
        return '<span class="flex items-center gap-2 text-xs font-semibold text-on-surface-variant px-2 py-1 bg-surface-container rounded-md"><span class="material-symbols-outlined text-[14px]" data-icon="description">description</span> PLANILHA</span>';
    }

    function statusBadgeHtml(status) {
        if (status === "conciliado") {
            return '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-secondary-container text-on-secondary-container">Matched</span>';
        }
        if (status === "pendente") {
            return '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-orange-100 text-orange-700">Pendente</span>';
        }
        return '<span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-error-container text-on-error-container">Divergente</span>';
    }

    function matchMeta(rule) {
        if (rule === "exact") {
            return { icon: "check_circle", percent: "100%", colorClass: "text-secondary", fill: "1" };
        }
        if (rule === "date_tolerance") {
            return { icon: "schedule", percent: "95%", colorClass: "text-on-surface-variant", fill: "0" };
        }
        if (rule === "description_similarity") {
            return { icon: "info", percent: "85%", colorClass: "text-on-surface-variant", fill: "0" };
        }
        return { icon: "warning", percent: "0%", colorClass: "text-error", fill: "1" };
    }

    function normalizeText(value) {
        return String(value || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .trim();
    }

    function sourceLabel(source) {
        return source === "bank" ? "banco" : "planilha";
    }

    function statusLabel(status) {
        const labels = {
            conciliado: "conciliado",
            pendente: "pendente",
            divergente: "divergente"
        };
        return labels[status] || "";
    }

    function rowMatchesQuery(row, query) {
        if (!query) {
            return true;
        }

        const searchableFields = [
            row.row_id,
            sourceLabel(row.source),
            formatDate(row.date),
            row.description,
            formatCurrency(row.amount),
            statusLabel(row.status),
            reasonLabel(row.reason),
            row.matched_row_id,
            matchMeta(row.match_rule).percent
        ];

        const haystack = normalizeText(searchableFields.join(" "));
        return haystack.includes(query);
    }

    function totalPages(totalItems) {
        return Math.max(1, Math.ceil(totalItems / ROWS_PER_PAGE));
    }

    function getPageRows(rows, page) {
        const start = (page - 1) * ROWS_PER_PAGE;
        return rows.slice(start, start + ROWS_PER_PAGE);
    }

    function renderPaginationControls() {
        if (!reconcilePageInfo || !reconcilePrevPage || !reconcileNextPage || !reconcilePageNumbers) {
            return;
        }

        const total = filteredReconcileRows.length;
        const pages = totalPages(total);
        const currentPage = Math.min(reconcileCurrentPage, pages);
        const start = total === 0 ? 0 : (currentPage - 1) * ROWS_PER_PAGE + 1;
        const end = total === 0 ? 0 : Math.min(currentPage * ROWS_PER_PAGE, total);

        reconcilePageInfo.textContent = `Mostrando ${start}-${end} de ${total} registros`;

        reconcilePrevPage.disabled = currentPage <= 1;
        reconcilePrevPage.classList.toggle("opacity-40", currentPage <= 1);
        reconcilePrevPage.classList.toggle("pointer-events-none", currentPage <= 1);
        reconcileNextPage.disabled = currentPage >= pages;
        reconcileNextPage.classList.toggle("opacity-40", currentPage >= pages);
        reconcileNextPage.classList.toggle("pointer-events-none", currentPage >= pages);

        reconcilePageNumbers.innerHTML = "";
        const startPage = Math.max(1, currentPage - 2);
        const endPage = Math.min(pages, startPage + 4);

        for (let page = startPage; page <= endPage; page += 1) {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = String(page);
            button.className = page === currentPage
                ? "w-8 h-8 flex items-center justify-center rounded-lg bg-primary text-on-primary font-bold text-xs"
                : "w-8 h-8 flex items-center justify-center rounded-lg hover:bg-surface-container-high text-on-surface-variant text-xs font-bold transition-colors";
            button.addEventListener("click", function () {
                reconcileCurrentPage = page;
                renderReconcileRows();
            });
            reconcilePageNumbers.appendChild(button);
        }
    }

    function renderReconcileRows() {
        if (!reconcileRowsBody) {
            return;
        }

        const items = getPageRows(filteredReconcileRows, reconcileCurrentPage);
        if (items.length === 0) {
            reconcileRowsBody.innerHTML = '<tr><td class="px-6 py-4 text-sm text-on-surface-variant" colspan="9">Sem linhas de conciliacao para exibir.</td></tr>';
            renderPaginationControls();
            return;
        }

        reconcileRowsBody.innerHTML = items.map(function (row) {
            const meta = matchMeta(row.match_rule);
            const reason = reasonLabel(row.reason);
            const paired = row.matched_row_id || "-";
            return `
<tr class="hover:bg-surface-container-low transition-colors group">
<td class="px-6 py-4 text-sm font-medium">${escapeHtml(row.row_id || "-")}</td>
<td class="px-6 py-4">${sourceBadgeHtml(row.source)}</td>
<td class="px-6 py-4 text-sm text-on-surface-variant">${escapeHtml(formatDate(row.date))}</td>
<td class="px-6 py-4 text-sm font-semibold">${escapeHtml(row.description || "-")}</td>
<td class="px-6 py-4 text-sm font-bold">${escapeHtml(formatCurrency(row.amount))}</td>
<td class="px-6 py-4">${statusBadgeHtml(row.status)}</td>
<td class="px-6 py-4">
<div class="flex items-center gap-1.5 ${meta.colorClass} font-bold text-sm">
<span class="material-symbols-outlined text-[16px]" data-icon="${meta.icon}" style="font-variation-settings: 'FILL' ${meta.fill};">${meta.icon}</span>
${meta.percent}
</div>
</td>
<td class="px-6 py-4 text-xs text-on-surface-variant">${escapeHtml(reason)}</td>
<td class="px-6 py-4 text-xs font-mono text-primary font-medium">${escapeHtml(paired)}</td>
</tr>`;
        }).join("");
        renderPaginationControls();
    }

    function applyReconcileFilter() {
        const query = normalizeText(reconcileSearchInput ? reconcileSearchInput.value : "");
        filteredReconcileRows = allReconcileRows.filter(function (row) {
            return rowMatchesQuery(row, query);
        });
        const pages = totalPages(filteredReconcileRows.length);
        reconcileCurrentPage = Math.min(reconcileCurrentPage, pages);
        renderReconcileRows();
    }

    function setReconcileRows(rows) {
        allReconcileRows = Array.isArray(rows) ? rows : [];
        reconcileCurrentPage = 1;
        applyReconcileFilter();
    }

    function renderReconcileTotals(payload) {
        const summary = payload.summary || {};
        const rows = Array.isArray(payload.reconciliation_rows) ? payload.reconciliation_rows : [];
        const totalBankRows = Number(summary.total_bank_rows || payload.bank_rows_parsed || 0);
        const totalSheetRows = Number(summary.total_sheet_rows || payload.sheet_rows_parsed || 0);
        const conciliated = Number(summary.conciliated_count || payload.conciliated_count || 0);
        const pending = Number(summary.pending_count || payload.pending_count || 0);
        const divergent = Number(summary.divergent_count || payload.divergent_count || 0);
        const pendingTotal = rows
            .filter(function (row) { return row.status !== "conciliado"; })
            .reduce(function (sum, row) { return sum + Math.abs(Number(row.amount || 0)); }, 0);
        const conciliatedRate = totalBankRows > 0 ? Math.round((conciliated / totalBankRows) * 100) : 0;

        if (metricConciliated) metricConciliated.textContent = String(conciliated);
        if (metricConciliatedRate) metricConciliatedRate.textContent = `${conciliatedRate}%`;
        if (metricPending) metricPending.textContent = String(pending);
        if (metricDivergent) metricDivergent.textContent = String(divergent).padStart(2, "0");
        if (metricPendingTotal) metricPendingTotal.textContent = formatCurrency(pendingTotal);
        if (metricBankRows) metricBankRows.textContent = String(totalBankRows);
        if (metricSheetRows) metricSheetRows.textContent = String(totalSheetRows);
        if (metricMissingInSheet) metricMissingInSheet.textContent = String(Number(payload.bank_unmatched_count || 0)).padStart(2, "0");
        if (metricMissingInBank) metricMissingInBank.textContent = String(Number(payload.sheet_unmatched_count || 0)).padStart(2, "0");
    }

    function setDownloadActions(apiBase, analysisId) {
        if (!analysisId) {
            return;
        }

        const xlsxUrl = `${apiBase}/reconcile-report/${analysisId}?format=xlsx`;
        const csvUrl = `${apiBase}/reconcile-report/${analysisId}?format=csv`;

        if (reconcileDownloadXlsx) {
            reconcileDownloadXlsx.dataset.url = xlsxUrl;
            if (!reconcileDownloadXlsx.dataset.bound) {
                reconcileDownloadXlsx.addEventListener("click", function () {
                    const url = reconcileDownloadXlsx.dataset.url;
                    if (url) {
                        window.open(url, "_blank", "noopener");
                    }
                });
                reconcileDownloadXlsx.dataset.bound = "1";
            }
        }

        if (reconcileDownloadCsv) {
            reconcileDownloadCsv.dataset.url = csvUrl;
            if (!reconcileDownloadCsv.dataset.bound) {
                reconcileDownloadCsv.addEventListener("click", function () {
                    const url = reconcileDownloadCsv.dataset.url;
                    if (url) {
                        window.open(url, "_blank", "noopener");
                    }
                });
                reconcileDownloadCsv.dataset.bound = "1";
            }
        }
    }

    async function runReconcileAndRender() {
        if (!updateReportGateState()) {
            showMissingFilesFeedback();
            return;
        }

        const apiBase = getApiBase();
        const formData = new FormData();
        formData.append("bank_file", bankFileInput.files[0]);
        formData.append("sheet_file", sheetFileInput.files[0]);

        const originalLabel = showReportBtn.innerHTML;
        isSubmitting = true;
        showReportBtn.innerHTML = '<span class="material-symbols-outlined text-2xl" data-icon="progress_activity">progress_activity</span> Processando...';
        updateReportGateState();
        setValidationMessage("");

        try {
            const response = await fetch(`${apiBase}/reconcile`, {
                method: "POST",
                body: formData
            });
            const payload = await parseJsonSafe(response);

            if (!response.ok) {
                const detail = payload.detail || "Falha ao processar conciliacao.";
                if (response.status === 422 && isSheetColumnValidationError(detail)) {
                    setSheetValidationErrorState(true);
                }
                throw new Error(response.status === 422 ? getFriendlyValidationMessage(detail) : detail);
            }

            setSheetValidationErrorState(false);
            renderReconcileTotals(payload);
            setReconcileRows(payload.reconciliation_rows || []);
            setDownloadActions(apiBase, payload.analysis_id);
            reconcileReportPreview.classList.remove("hidden");
            reportFocusMode = true;
            setTopNavHidden(isReportInViewport());
            reconcileReportPreview.scrollIntoView({ behavior: "smooth", block: "start" });
            requestAnimationFrame(function () {
                setTopNavHidden(isReportInViewport());
            });
        } catch (error) {
            const message = error instanceof Error ? error.message : "Erro inesperado.";
            setValidationMessage(message);
        } finally {
            isSubmitting = false;
            showReportBtn.innerHTML = originalLabel;
            updateReportGateState({ clearMessage: false });
        }
    }

    if (bankFileInput) {
        bankFileInput.addEventListener("change", function () {
            updateReportGateState();
            if (reconcileReportPreview && !reconcileReportPreview.classList.contains("hidden")) {
                reconcileReportPreview.classList.add("hidden");
                reportFocusMode = false;
                setTopNavHidden(false);
            }
        });
    }

    if (sheetFileInput) {
        sheetFileInput.addEventListener("change", function () {
            updateReportGateState();
            if (reconcileReportPreview && !reconcileReportPreview.classList.contains("hidden")) {
                reconcileReportPreview.classList.add("hidden");
                reportFocusMode = false;
                setTopNavHidden(false);
            }
        });
    }

    window.addEventListener("scroll", handleScrollNavFocus, { passive: true });
    window.addEventListener("resize", handleScrollNavFocus);

    if (reconcileSearchInput) {
        reconcileSearchInput.addEventListener("input", function () {
            reconcileCurrentPage = 1;
            applyReconcileFilter();
        });
    }

    if (reconcileSearchClear) {
        reconcileSearchClear.addEventListener("click", function () {
            if (!reconcileSearchInput) {
                return;
            }
            reconcileSearchInput.value = "";
            reconcileSearchInput.focus();
            reconcileCurrentPage = 1;
            applyReconcileFilter();
        });
    }

    if (reconcilePrevPage) {
        reconcilePrevPage.addEventListener("click", function () {
            reconcileCurrentPage = Math.max(1, reconcileCurrentPage - 1);
            renderReconcileRows();
        });
    }

    if (reconcileNextPage) {
        reconcileNextPage.addEventListener("click", function () {
            const pages = totalPages(filteredReconcileRows.length);
            reconcileCurrentPage = Math.min(pages, reconcileCurrentPage + 1);
            renderReconcileRows();
        });
    }

    updateReportGateState();
    setReconcileRows([]);

    if (showReportBtn && reconcileReportPreview) {
        showReportBtn.addEventListener("click", function () {
            runReconcileAndRender();
        });
    }

    if (topCtaStart) {
        topCtaStart.addEventListener("click", function () {
            focusUploadSection();
        });
    }

