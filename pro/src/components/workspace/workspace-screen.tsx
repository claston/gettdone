"use client";

import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  Clock3,
  Download,
  FileText,
  Filter,
  LoaderCircle,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Redo2,
  RefreshCw,
  RotateCcw,
  Search,
  Undo2,
  XCircle,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { reviewReturnTo } from "@/domain/review-return";
import { useEffect, useMemo, useState } from "react";

import { useOptionalProSession } from "@/components/pro-session-gate";
import { emitWorkspaceEvent } from "@/domain/workspace-events";
import {
  resolveWorkspaceRepository,
  type WorkspaceDataMode,
} from "@/data/workspace-repository";
import {
  sortConversionsForReview,
  filterTransactions,
} from "@/domain/workspace";
import type {
  Conversion,
  SaveStatus,
  WorkspaceRepository,
  TransactionFilter,
  SourceRegion,
} from "@/domain/workspace";
import { useWorkspaceController } from "./use-workspace-controller";
import { DocumentViewer } from "./document-viewer";
import { TransactionGrid } from "./transaction-grid";
import { FinancialReportReview } from "./financial-report-review";
import { ReviewPanels } from "./review-panels";
import {
  sourceLocation,
  transactionRegions,
  type PdfNavigationRequest,
  type PdfReadingPosition,
} from "./source-location";

interface WorkspaceScreenProps {
  clientMode?: boolean;
  batchId: string;
  initialConversionId?: string;
  repository?: WorkspaceRepository;
  dataMode?: WorkspaceDataMode;
  pollIntervalMs?: number;
  returnTo?: string;
}

const statusContent: Record<
  Conversion["status"],
  { label: string; tone: string; icon: typeof Check }
> = {
  NEEDS_REVIEW: { label: "Revisar", tone: "warning", icon: AlertCircle },
  READY: { label: "Pronto", tone: "success", icon: CheckCircle2 },
  PROCESSING: { label: "Processando", tone: "info", icon: LoaderCircle },
  FAILED: { label: "Falha", tone: "danger", icon: XCircle },
  REVIEWED: { label: "Revisado", tone: "muted", icon: Check },
  EXPORTED: { label: "Exportado", tone: "muted", icon: Download },
};

function StatusBadge({ status }: { status: Conversion["status"] }) {
  const content = statusContent[status];
  const Icon = content.icon;
  return (
    <span className={`status-badge status-${content.tone}`}>
      <Icon size={12} className={status === "PROCESSING" ? "spin" : ""} />
      {content.label}
    </span>
  );
}

function SaveIndicator({
  status,
  error,
  onRetry,
}: {
  status: SaveStatus;
  error: string | null;
  onRetry(): void;
}) {
  if (status === "SAVING") {
    return (
      <span className="save-indicator">
        <LoaderCircle size={14} className="spin" /> Salvando…
      </span>
    );
  }
  if (status === "DIRTY") {
    return (
      <span className="save-indicator">
        <Clock3 size={14} /> Alterações pendentes
      </span>
    );
  }
  if (status === "ERROR") {
    return (
      <button
        className="save-indicator save-error"
        onClick={onRetry}
        title={error ?? undefined}
      >
        <AlertCircle size={14} /> Falha ao salvar · tentar novamente
      </button>
    );
  }
  return (
    <span className="save-indicator save-ok">
      <Check size={14} /> Salvo
    </span>
  );
}

function ReviewRail({ userName }: { userName: string }) {
  const avatar = userName
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toLocaleUpperCase("pt-BR") ?? "")
    .join("");

  return (
    <aside className="review-rail" aria-label="Navegação rápida">
      <Link
        href="/dashboard"
        className="rail-brand"
        aria-label="OFX Simples PRO"
      >
        <Zap size={20} fill="currentColor" />
      </Link>
      <Link href="/conversions" className="rail-link" aria-label="Conversões">
        <FileText size={20} />
      </Link>
      <Link
        href="/dashboard"
        className="rail-link"
        aria-label="Voltar ao dashboard"
      >
        <Menu size={20} />
      </Link>
      <span className="rail-spacer" />
      <span className="rail-avatar" title={userName}>
        {avatar || "OF"}
      </span>
    </aside>
  );
}

function ConversionNavigator({
  clientMode = false,
  conversions,
  activeId,
  collapsed,
  onCollapse,
  onSelect,
  onNext,
}: {
  clientMode?: boolean;
  conversions: Conversion[];
  activeId: string;
  collapsed: boolean;
  onCollapse(): void;
  onSelect(id: string): void;
  onNext(): void;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("ALL");
  const ordered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("pt-BR");
    return sortConversionsForReview(conversions).filter((conversion) => {
      const matchesSearch =
        !query ||
        conversion.fileName.toLocaleLowerCase("pt-BR").includes(query) ||
        conversion.bankName.toLocaleLowerCase("pt-BR").includes(query);
      const matchesFilter =
        filter === "ALL" ||
        (filter === "PENDING" &&
          ["NEEDS_REVIEW", "READY"].includes(conversion.status)) ||
        conversion.status === filter;
      return matchesSearch && matchesFilter;
    });
  }, [conversions, filter, search]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const index = ordered.findIndex((item) => item.id === activeId);
    const direction = event.key === "ArrowDown" ? 1 : -1;
    const next = ordered[(index + direction + ordered.length) % ordered.length];
    if (next) onSelect(next.id);
  };

  if (collapsed) {
    return (
      <aside className="conversion-panel collapsed">
        <button
          className="icon-button"
          onClick={onCollapse}
          aria-label="Expandir lista de conversões"
        >
          <PanelLeftOpen size={18} />
        </button>
        <span className="vertical-label">{conversions.length} arquivos</span>
      </aside>
    );
  }

  return (
    <aside className="conversion-panel">
      <div className="panel-heading compact">
        <div>
          <span className="eyebrow">
            {clientMode ? "Extratos do cliente" : "Navegação do lote"}
          </span>
          <h2>
            Conversões <span>{conversions.length}</span>
          </h2>
        </div>
        <button
          className="icon-button"
          onClick={onCollapse}
          aria-label="Recolher lista de conversões"
        >
          <PanelLeftClose size={18} />
        </button>
      </div>
      <div className="conversion-tools">
        <label className="search-box">
          <Search size={15} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Buscar arquivo ou banco"
            aria-label="Buscar conversões"
          />
        </label>
        <label className="select-box" aria-label="Filtrar conversões">
          <Filter size={14} />
          <select
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="ALL">Todos</option>
            <option value="PENDING">Pendentes</option>
            <option value="PROCESSING">Processando</option>
            <option value="FAILED">Com falha</option>
            <option value="REVIEWED">Revisados</option>
          </select>
        </label>
      </div>
      <div
        className="conversion-list"
        role="listbox"
        aria-label={clientMode ? "Extratos do cliente" : "Conversões do lote"}
        tabIndex={0}
        onKeyDown={handleKeyDown}
      >
        {ordered.map((conversion) => (
          <button
            key={conversion.id}
            role="option"
            aria-selected={conversion.id === activeId}
            className={`conversion-card ${conversion.id === activeId ? "active" : ""}`}
            onClick={() => onSelect(conversion.id)}
          >
            <span className="bank-mark">
              {conversion.bankName.slice(0, 2).toUpperCase()}
            </span>
            <span className="conversion-main">
              <span className="conversion-bank">{conversion.bankName}</span>
              <span className="conversion-file">{conversion.fileName}</span>
              <span className="conversion-meta">
                {conversion.fileName.split(".").pop()?.toUpperCase()} ·{" "}
                {conversion.period} · {conversion.transactionCount} lanç.
              </span>
            </span>
            <span className="conversion-state">
              <StatusBadge status={conversion.status} />
              {conversion.suspectedCount > 0 && (
                <span className="suspect-count">
                  {conversion.suspectedCount}
                </span>
              )}
            </span>
          </button>
        ))}
        {ordered.length === 0 && (
          <div className="empty-list">Nenhuma conversão encontrada.</div>
        )}
      </div>
      <button className="next-pending" onClick={onNext}>
        Próxima pendente <ArrowRight size={16} />
      </button>
    </aside>
  );
}

export function WorkspaceScreen({
  batchId,
  initialConversionId,
  repository,
  dataMode = "api",
  pollIntervalMs,
  returnTo,
  clientMode = false,
}: WorkspaceScreenProps) {
  const session = useOptionalProSession();
  const activeRepository = repository ?? resolveWorkspaceRepository(dataMode);
  const reviewBasePath = clientMode
    ? "/client-review"
    : dataMode === "demo"
      ? "/demo/review"
      : "/review";
  const controller = useWorkspaceController(
    batchId,
    activeRepository,
    initialConversionId,
    reviewBasePath,
    pollIntervalMs,
  );
  const [navigatorCollapsed, setNavigatorCollapsed] = useState(false);
  const [focusMode, setFocusMode] = useState(true);
  const [groupByPage, setGroupByPage] = useState(false);
  const [pages, setPages] = useState<Record<string, number>>({});
  const [navigation, setNavigation] = useState<
    (PdfNavigationRequest & { id: string }) | null
  >(null);
  const [readyDocument, setReadyDocument] = useState<string | null>(null);
  const [reading, setReading] = useState<
    (PdfReadingPosition & { id: string }) | null
  >(null);
  const [transactionFilter, setTransactionFilter] =
    useState<TransactionFilter>("ALL");
  const [editingDocument, setEditingDocument] = useState<string | null>(null);
  const [marking, setMarking] = useState<{
    document: string;
    transactionId: string;
  } | null>(null);
  const [markError, setMarkError] = useState<string | null>(null);
  useEffect(() => {
    if (transactionFilter === "SUSPECTED")
      emitWorkspaceEvent(activeRepository, "suspicious_filter_used");
  }, [transactionFilter, activeRepository]);
  const activeConversion = controller.activeConversion;
  const activeNavigation = useMemo(() => {
    if (
      !activeConversion ||
      navigation?.id !== `${activeConversion.id}:${activeConversion.pdf?.url}`
    )
      return null;
    const row = activeConversion.transactions.find(
      (item) => item.id === navigation.transactionId,
    );
    if (navigation.transactionId && !row) return null;
    if (row) {
      const regions = transactionRegions(row);
      if (JSON.stringify(regions) !== JSON.stringify(navigation.regions))
        return { ...navigation, regions, scroll: false };
    }
    return navigation;
  }, [activeConversion, navigation]);

  if (
    marking &&
    (marking.document !== activeConversion?.id ||
      marking.transactionId !== activeNavigation?.transactionId ||
      !activeConversion?.transactions.some(
        (row) => row.id === marking.transactionId && !row.isDeleted,
      ))
  )
    setMarking(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (document.querySelector("dialog[open]")) return;
      if (controller.actionBusy || controller.exportState === "EXPORTING")
        return;
      if (event.altKey && ["PageDown", "PageUp"].includes(event.key)) {
        const ordered = sortConversionsForReview(
          controller.batch?.conversions ?? [],
        );
        const index = ordered.findIndex(
          (row) => row.id === controller.activeConversion?.id,
        );
        const next =
          ordered[
            (index + (event.key === "PageDown" ? 1 : -1) + ordered.length) %
              ordered.length
          ];
        if (next) {
          event.preventDefault();
          controller.selectConversion(next.id);
        }
        return;
      }
      if (
        marking &&
        (event.key === "Escape" ||
          ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z"))
      ) {
        event.preventDefault();
        setMarking(null);
        setMarkError(null);
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        // Commit the focused amount/date before checking the save guard. A
        // draft must never be left behind by the review shortcut.
        if (event.target instanceof HTMLInputElement) event.target.blur();
        setTimeout(() => {
          if (
            !document.querySelector(
              '.transactions-panel input[aria-invalid="true"]',
            )
          )
            void controller.markReviewed();
        }, 0);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        if (
          (event.target as HTMLElement).matches(
            "input, textarea, [contenteditable=true]",
          )
        )
          return;
        event.preventDefault();
        controller.undo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [controller, marking]);

  if (controller.loadState === "LOADING") {
    return (
      <main className="screen-state">
        <LoaderCircle className="spin" size={30} />
        <h1>Abrindo workspace PRO</h1>
        <p>
          {dataMode === "demo"
            ? "Preparando os dados de demonstração…"
            : "Carregando o lote e as conversões…"}
        </p>
      </main>
    );
  }
  if (controller.loadState === "ERROR" || !controller.batch) {
    return (
      <main className="screen-state error">
        <AlertCircle size={30} />
        <h1>Não foi possível abrir o lote</h1>
        <p>{controller.loadError}</p>
        <button className="secondary-button" onClick={controller.retryLoad}>
          Tentar abrir novamente
        </button>
      </main>
    );
  }

  const { batch } = controller;
  const selectedTransaction = activeConversion?.transactions.find(
    (row) => row.id === activeNavigation?.transactionId,
  );
  const drawingFor =
    marking?.document === activeConversion?.id &&
    marking?.transactionId === selectedTransaction?.id &&
    selectedTransaction &&
    !selectedTransaction.isDeleted
      ? {
          id: selectedTransaction.id,
          description: selectedTransaction.description,
        }
      : null;
  const selectPdfTransaction = (
    id: string,
    page = activeConversion ? (pages[activeConversion.id] ?? 1) : 1,
    fromEditor = false,
  ) => {
    if (!activeConversion) return;
    const row = activeConversion.transactions.find((item) => item.id === id);
    if (!row || row.isDeleted) return;
    setMarking(null);
    if (!fromEditor) setReading(null);
    setPages((current) => ({ ...current, [activeConversion.id]: page }));
    setNavigation((current) => ({
      ...(sourceLocation(row) ?? { transactionId: row.id, regions: [] }),
      page,
      scroll: false,
      origin: fromEditor ? "pdf" : "pdf-selection",
      id: `${activeConversion.id}:${activeConversion.pdf?.url}`,
      token: (current?.token ?? 0) + 1,
    }));
  };
  const changeReviewRegions = (regions: SourceRegion[]) => {
    if (!selectedTransaction || !activeConversion) return;
    const error = controller.setReviewRegions(selectedTransaction.id, regions);
    setMarkError(error);
    if (!error) {
      setMarking(null);
      setReading(null);
      setNavigation(
        (current) =>
          current && {
            ...current,
            regions: transactionRegions({
              ...selectedTransaction,
              reviewRegions: regions,
            }),
            origin: "pdf-selection",
            scroll: false,
            token: current.token + 1,
          },
      );
    }
  };
  const reviewed = batch.conversions.filter(
    (item) => item.status === "REVIEWED" || item.status === "EXPORTED",
  ).length;
  const pending = batch.conversions.filter(
    (item) => item.status === "NEEDS_REVIEW" || item.status === "READY",
  ).length;
  const progress = Math.round(
    (reviewed / Math.max(1, batch.conversions.length)) * 100,
  );
  const busy = controller.actionBusy || controller.exportState === "EXPORTING";
  const canReview = !["DIRTY", "SAVING", "ERROR"].includes(
    controller.saveStatus,
  );

  return (
    <main className={`workspace ${focusMode ? "focus-mode" : ""}`}>
      <ReviewRail
        userName={
          dataMode === "demo"
            ? "Usuário de demonstração"
            : (session?.user.name ?? "Usuário PRO")
        }
      />
      <header className="workspace-header">
        <div className="batch-heading">
          <Link href={reviewReturnTo(returnTo)} className="back-link">
            <ChevronLeft size={16} />{" "}
            {clientMode || returnTo === "/dashboard"
              ? "Extratos recebidos"
              : returnTo
                ? reviewReturnTo(returnTo).startsWith("/works/")
                  ? "Voltar ao trabalho"
                  : "Voltar às conversões"
                : "Lotes"}
          </Link>
          <div>
            <span className="eyebrow">{batch.clientName}</span>
            <h1>{batch.name}</h1>
          </div>
          {dataMode === "demo" && (
            <span className="demo-badge">Dados de demonstração</span>
          )}
        </div>
        <div className="batch-progress">
          <div className="progress-copy">
            <strong>
              {reviewed} de {batch.conversions.length}
            </strong>
            <span>arquivos revisados</span>
          </div>
          <div className="progress-track">
            <span style={{ width: `${progress}%` }} />
          </div>
          <span className="pending-copy">{pending} pendências</span>
        </div>
        <div className="header-actions">
          <span className="quota">
            Cota{" "}
            <strong>
              {batch.quota.used}/{batch.quota.limit}
            </strong>
          </span>
          <SaveIndicator
            status={controller.saveStatus}
            error={controller.saveError}
            onRetry={controller.retrySave}
          />
          <button
            className="icon-button"
            onClick={() => void controller.refreshBatch()}
            disabled={controller.refreshState === "REFRESHING"}
            aria-label="Atualizar dados do lote"
            title={controller.refreshError ?? "Atualizar dados do lote"}
          >
            <RefreshCw
              size={16}
              className={
                controller.refreshState === "REFRESHING" ? "spin" : undefined
              }
            />
          </button>
          {activeConversion?.downloadUrl && (
            <a
              className="secondary-button"
              href={
                canReview && !busy ? activeConversion.downloadUrl : undefined
              }
              aria-disabled={!canReview || busy}
              download
            >
              <Download size={15} /> Baixar{" "}
              {activeConversion.financialDocument ? "Excel" : "OFX"}
            </a>
          )}
          <button
            className="icon-button"
            onClick={() => setFocusMode((value) => !value)}
            title="Alternar modo de foco"
          >
            <PanelLeftClose size={17} />
          </button>
          <button
            className="secondary-button"
            onClick={() => void controller.exportBatch()}
            disabled={busy}
          >
            <Download size={15} />{" "}
            {controller.exportState === "EXPORTING"
              ? "Preparando…"
              : clientMode
                ? "Exportar extratos"
                : "Exportar lote"}
          </button>
          <button
            className="primary-button"
            onClick={() => void controller.markReviewed()}
            disabled={
              !activeConversion ||
              busy ||
              !canReview ||
              !["READY", "NEEDS_REVIEW"].includes(activeConversion.status)
            }
          >
            <CheckCircle2 size={16} /> Marcar revisado
          </button>
        </div>
      </header>

      <div className="workspace-alerts">
        {batch.conversions.length > 0 &&
          reviewed === batch.conversions.length && (
            <div className="workspace-complete" role="status">
              {clientMode
                ? "Revisão dos extratos concluída. Você já pode exportar os arquivos."
                : "Revisão do lote concluída. Você já pode exportar os arquivos."}
            </div>
          )}
        {controller.exportError && (
          <div role="alert">
            <span>{controller.exportError}</span>
            <button
              className="secondary-button small"
              onClick={() => void controller.exportBatch()}
            >
              Tentar exportar novamente
            </button>
          </div>
        )}
        {controller.actionError && (
          <div role="alert">
            <span>{controller.actionError}</span>
            <button
              className="secondary-button small"
              onClick={controller.retryAction}
            >
              Tentar ação novamente
            </button>
          </div>
        )}
        {controller.refreshError && (
          <div role="alert">
            <span>{controller.refreshError}</span>
            <button
              className="secondary-button small"
              onClick={() => void controller.refreshBatch()}
            >
              Tentar atualizar novamente
            </button>
          </div>
        )}
      </div>
      <div className="workspace-body" inert={busy}>
        <ConversionNavigator
          clientMode={clientMode}
          conversions={batch.conversions}
          activeId={activeConversion?.id ?? ""}
          collapsed={navigatorCollapsed}
          onCollapse={() => setNavigatorCollapsed((value) => !value)}
          onSelect={(id) => {
            setNavigation(null);
            setReading(null);
            setMarking(null);
            setMarkError(null);
            setEditingDocument(null);
            setTransactionFilter("ALL");
            controller.selectConversion(id);
          }}
          onNext={() => {
            setNavigation(null);
            setReading(null);
            setMarking(null);
            setMarkError(null);
            setEditingDocument(null);
            setTransactionFilter("ALL");
            controller.goToNextPending();
          }}
        />
        {activeConversion ? (
          <ReviewPanels>
            <DocumentViewer
              key={`viewer-${activeConversion.id}`}
              conversion={activeConversion}
              repository={activeRepository}
              navigation={activeNavigation}
              review={{
                transactions: filterTransactions(
                  activeConversion.transactions,
                  transactionFilter,
                ),
                selectedId: activeNavigation?.transactionId,
                drawingFor,
                paused: editingDocument === activeConversion.id,
                onSelect: selectPdfTransaction,
                onStartDrawing: () => {
                  if (selectedTransaction && !selectedTransaction.isDeleted) {
                    setMarkError(null);
                    setEditingDocument(null);
                    setMarking({
                      document: activeConversion.id,
                      transactionId: selectedTransaction.id,
                    });
                  }
                },
                onCancelDrawing: () => {
                  setMarking(null);
                  setMarkError(null);
                },
                onCreateRegion: (region) =>
                  changeReviewRegions([
                    ...(selectedTransaction?.reviewRegions ?? []),
                    region,
                  ]),
                onClearRegions: () => changeReviewRegions([]),
              }}
              onReadingPosition={(position) => {
                if (drawingFor) return;
                const id = `${activeConversion.id}:${activeConversion.pdf?.url}`;
                setReading({ ...position, id });
                setNavigation((current) =>
                  current?.id === id &&
                  (current.origin === "grid" ||
                    current.origin === "pdf-selection")
                    ? {
                        ...current,
                        origin: "pdf",
                        scroll: false,
                        token: current.token + 1,
                      }
                    : current,
                );
              }}
              onAvailability={(available) =>
                setReadyDocument(
                  available
                    ? `${activeConversion.id}:${activeConversion.pdf?.url}`
                    : null,
                )
              }
              page={pages[activeConversion.id] ?? 1}
              setPage={(page) =>
                setPages((current) => ({
                  ...current,
                  [activeConversion.id]: page,
                }))
              }
            />
            {activeConversion.status === "FAILED" ? (
              <section className="transactions-panel viewer-state error">
                <RotateCcw size={28} />
                {activeConversion.errorCode === "upload_failed" ? (
                  <>
                    <h3>Falha no envio do arquivo</h3>
                    <p>
                      Selecione este arquivo novamente em Nova conversão. Os
                      demais arquivos do lote continuam sendo processados.
                    </p>
                    <Link className="primary-button" href="/conversions">
                      Enviar arquivo novamente
                    </Link>
                  </>
                ) : activeConversion.ocrMode === "pro" ? (
                  <>
                    <h3>Conversão PRO interrompida</h3>
                    <p>
                      {activeConversion.errorMessage ??
                        "Não foi possível concluir a leitura deste PDF."}
                    </p>
                    <p>
                      Uma nova leitura exige um novo envio e pode consumir
                      páginas. Confira o motivo da falha antes de reenviar.
                    </p>
                    <Link className="primary-button" href="/conversions">
                      Enviar PDF em nova conversão
                    </Link>
                  </>
                ) : (
                  <>
                    <h3>Conversão interrompida</h3>
                    <p>
                      {activeConversion.errorMessage ??
                        "Tente processar este arquivo novamente sem bloquear o restante do lote."}
                    </p>
                    <button
                      className="primary-button"
                      onClick={() => void controller.retryConversion()}
                    >
                      Tentar novamente
                    </button>
                  </>
                )}
              </section>
            ) : activeConversion.status === "PROCESSING" ? (
              <section className="transactions-panel viewer-state">
                <LoaderCircle className="spin" size={28} />
                <h3>Aguardando lançamentos</h3>
                <p>
                  Esta tela atualiza o lote automaticamente.{" "}
                  {activeConversion.pdf
                    ? "O PDF original continua disponível para consulta."
                    : "Os lançamentos aparecerão aqui ao concluir o processamento."}
                </p>
                {controller.refreshError && (
                  <p className="refresh-error" role="alert">
                    {controller.refreshError}
                  </p>
                )}
                <button
                  className="secondary-button"
                  onClick={() => void controller.refreshBatch()}
                  disabled={controller.refreshState === "REFRESHING"}
                >
                  <RefreshCw
                    size={15}
                    className={
                      controller.refreshState === "REFRESHING"
                        ? "spin"
                        : undefined
                    }
                  />
                  {controller.refreshState === "REFRESHING"
                    ? "Atualizando…"
                    : "Atualizar agora"}
                </button>
              </section>
            ) : activeConversion.financialDocument ? (
              <FinancialReportReview
                key={activeConversion.id}
                report={activeConversion.financialDocument}
                downloadUrl={activeConversion.downloadUrl}
                onSource={(id) => {
                  const event = activeConversion.financialDocument?.events.find(
                    (item) => item.event_id === id,
                  );
                  const page = event?.source_refs[0]?.page;
                  if (!page) return;
                  setMarking(null);
                  setReading(null);
                  setPages((current) => ({
                    ...current,
                    [activeConversion.id]: page,
                  }));
                  setNavigation((current) => ({
                    page,
                    regions: [],
                    origin: "grid",
                    id: `${activeConversion.id}:${activeConversion.pdf?.url}`,
                    token: (current?.token ?? 0) + 1,
                  }));
                }}
              />
            ) : (
              <TransactionGrid
                key={`grid-${activeConversion.id}`}
                conversion={activeConversion}
                onUpdate={controller.updateField}
                onAdd={controller.addTransaction}
                onRemove={controller.removeTransaction}
                onRestore={controller.restoreTransaction}
                groupByPage={groupByPage}
                onGroupChange={setGroupByPage}
                filterValue={transactionFilter}
                onFilterChange={(value) => {
                  setTransactionFilter(value);
                  setMarking(null);
                  setMarkError(null);
                  setReading(null);
                }}
                onSelectTransaction={(id, editing) =>
                  selectPdfTransaction(id, undefined, editing)
                }
                onEditingChange={(editing) =>
                  setEditingDocument(editing ? activeConversion.id : null)
                }
                syncPaused={Boolean(drawingFor)}
                selectedFromPdf={activeNavigation?.origin === "pdf-selection"}
                activePage={
                  activeConversion.pdf &&
                  (activeConversion.pdf.url.startsWith("/mock-statement") ||
                    readyDocument ===
                      `${activeConversion.id}:${activeConversion.pdf.url}`)
                    ? (pages[activeConversion.id] ?? 1)
                    : null
                }
                onLocateTransaction={(transactionId, options) => {
                  setMarking(null);
                  const transaction = activeConversion.transactions.find(
                    (row) => row.id === transactionId,
                  );
                  const target = transaction && sourceLocation(transaction);
                  if (!target) return;
                  if (options?.scroll !== false)
                    setPages((current) => ({
                      ...current,
                      [activeConversion.id]: target.page,
                    }));
                  setNavigation((current) => ({
                    ...target,
                    ...options,
                    origin: options?.origin ?? "grid",
                    id: `${activeConversion.id}:${activeConversion.pdf?.url}`,
                    token: (current?.token ?? 0) + 1,
                  }));
                }}
                locatedTransactionId={activeNavigation?.transactionId}
                onClearSource={() => {
                  setNavigation(null);
                  setMarking(null);
                }}
                followPdf={activeNavigation?.origin !== "grid"}
                readingPosition={
                  reading?.id ===
                  `${activeConversion.id}:${activeConversion.pdf?.url}`
                    ? reading
                    : undefined
                }
                onSourcePage={(page) => {
                  setMarking(null);
                  setReading(null);
                  setPages((current) => ({
                    ...current,
                    [activeConversion.id]: page,
                  }));
                  setNavigation((current) => ({
                    id: `${activeConversion.id}:${activeConversion.pdf?.url}`,
                    page,
                    regions: [],
                    token: (current?.token ?? 0) + 1,
                  }));
                }}
              />
            )}
          </ReviewPanels>
        ) : (
          <div className="viewer-state">
            <FileText size={28} />
            <h3>Selecione uma conversão</h3>
          </div>
        )}
      </div>

      {markError && (
        <div className="pdf-mark-error" role="alert">
          {markError}
        </div>
      )}

      <footer className="workspace-footer">
        <span>
          <kbd>Alt</kbd> + <kbd>↑/↓</kbd> linhas · <kbd>PgUp/PgDn</kbd> arquivos
        </span>
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd> navegar arquivos
        </span>
        <span>
          <kbd>Ctrl</kbd> + <kbd>Z</kbd> desfazer
        </span>
        <span>
          <kbd>Ctrl</kbd> + <kbd>Enter</kbd> marcar revisado
        </span>
        <span className="footer-spacer" />
        <button
          className="footer-action"
          onClick={controller.undo}
          disabled={busy || !controller.canUndo}
        >
          <Undo2 size={14} /> Desfazer
        </button>
        <button
          className="footer-action"
          disabled={busy}
          onClick={() => {
            setNavigation(null);
            setReading(null);
            setMarking(null);
            setMarkError(null);
            setEditingDocument(null);
            setTransactionFilter("ALL");
            controller.goToNextPending();
          }}
        >
          Próxima pendente <Redo2 size={14} />
        </button>
      </footer>

      {controller.exportState === "DONE" && controller.exportResult && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          <div>
            <strong>Exportação concluída</strong>
            <span>
              {controller.exportResult.fileName} ·{" "}
              {controller.exportResult.conversionCount} conversões
            </span>
          </div>
          <button onClick={controller.dismissExport} aria-label="Fechar aviso">
            ×
          </button>
        </div>
      )}
    </main>
  );
}
