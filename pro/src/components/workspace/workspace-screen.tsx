"use client";

import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  ExternalLink,
  FileText,
  Filter,
  LoaderCircle,
  Menu,
  Minus,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Redo2,
  RotateCcw,
  Search,
  Trash2,
  Undo2,
  XCircle,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { mockWorkspaceRepository } from "@/data/mock-workspace-repository";
import {
  filterTransactions,
  formatCurrency,
  sortConversionsForReview,
  transactionHasState,
} from "@/domain/workspace";
import type {
  Conversion,
  SaveStatus,
  Transaction,
  TransactionField,
  TransactionFilter,
  WorkspaceRepository,
} from "@/domain/workspace";
import { useWorkspaceController } from "./use-workspace-controller";

interface WorkspaceScreenProps {
  batchId: string;
  initialConversionId?: string;
  repository?: WorkspaceRepository;
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

function ReviewRail() {
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
      <span className="rail-avatar" title="Ana Souza">
        AS
      </span>
    </aside>
  );
}

function ConversionNavigator({
  conversions,
  activeId,
  collapsed,
  onCollapse,
  onSelect,
  onNext,
}: {
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
          <span className="eyebrow">Navegação do lote</span>
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
        aria-label="Conversões do lote"
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

function PdfViewer({ conversion }: { conversion: Conversion }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState<"page-width" | number>("page-width");

  if (conversion.status === "PROCESSING") {
    return (
      <div className="viewer-state">
        <LoaderCircle className="spin" size={28} />
        <h3>Processando extrato</h3>
        <p>Você pode revisar outro arquivo enquanto este termina.</p>
      </div>
    );
  }
  if (!conversion.pdf || conversion.status === "FAILED") {
    return (
      <div className="viewer-state error">
        <XCircle size={30} />
        <h3>Não foi possível processar o PDF</h3>
        <p>O restante do lote continua disponível.</p>
      </div>
    );
  }

  const previewRows = [
    ["02/09/2026", "PIX recebido — Comércio Horizonte", "145,90", "38.566,07"],
    ["03/09/2026", "Pagamento de boleto", "− 283,13", "38.282,94"],
    ["04/09/2026", "Transferência entre contas", "− 420,36", "37.862,58"],
    ["05/09/2026", "Tarifa pacote de serviços", "− 557,59", "37.304,99"],
    ["06/09/2026", "Recebimento via cartão", "694,82", "37.999,81"],
    ["09/09/2026", "PIX enviado — Fornecedor Delta", "− 832,05", "37.167,76"],
    ["10/09/2026", "Aplicação automática", "− 969,28", "36.198,48"],
    ["11/09/2026", "Resgate de investimento", "1.106,51", "37.304,99"],
    ["12/09/2026", "TED recebida", "1.243,74", "38.548,73"],
  ];
  return (
    <section className="pdf-panel" aria-label="Documento original">
      <div className="panel-toolbar">
        <div className="toolbar-title">
          <FileText size={16} />
          <span>{conversion.fileName}</span>
        </div>
        <div className="pdf-controls">
          <button
            className="icon-button"
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={page === 1}
            aria-label="Página anterior"
          >
            <ChevronLeft size={17} />
          </button>
          <span>
            Página <strong>{page}</strong> de {conversion.pdf.pageCount}
          </span>
          <button
            className="icon-button"
            onClick={() =>
              setPage((current) =>
                Math.min(conversion.pdf!.pageCount, current + 1),
              )
            }
            disabled={page === conversion.pdf.pageCount}
            aria-label="Próxima página"
          >
            <ChevronRight size={17} />
          </button>
          <span className="toolbar-divider" />
          <button
            className="icon-button"
            onClick={() =>
              setZoom((value) =>
                Math.max(70, (typeof value === "number" ? value : 100) - 10),
              )
            }
            aria-label="Diminuir zoom"
          >
            <Minus size={16} />
          </button>
          <button className="zoom-label" onClick={() => setZoom("page-width")}>
            {zoom === "page-width" ? "Ajustar" : `${zoom}%`}
          </button>
          <button
            className="icon-button"
            onClick={() =>
              setZoom((value) =>
                Math.min(160, (typeof value === "number" ? value : 100) + 10),
              )
            }
            aria-label="Aumentar zoom"
          >
            <Plus size={16} />
          </button>
          <a
            className="icon-button"
            href={`${conversion.pdf.url}#page=${page}`}
            target="_blank"
            rel="noreferrer"
            aria-label="Abrir PDF original"
          >
            <ExternalLink size={15} />
          </a>
        </div>
      </div>
      <div className="pdf-stage">
        <div
          className="pdf-document"
          style={{ width: zoom === "page-width" ? "100%" : `${zoom}%` }}
          role="document"
          aria-label="Visualizador do extrato original"
        >
          <div className="mock-pdf-brand">
            <strong>{conversion.bankName}</strong>
            <span>Extrato de conta corrente</span>
          </div>
          <div className="mock-pdf-summary">
            <div>
              <small>Agência</small>
              <strong>0192</strong>
            </div>
            <div>
              <small>Conta</small>
              <strong>45.810-7</strong>
            </div>
            <div>
              <small>Período</small>
              <strong>01/09/2026–30/09/2026</strong>
            </div>
          </div>
          <h3>Movimentação da conta</h3>
          <div className="mock-pdf-table">
            <div className="mock-pdf-row head">
              <span>Data</span>
              <span>Histórico</span>
              <span>Valor (R$)</span>
              <span>Saldo (R$)</span>
            </div>
            {previewRows.map((row, index) => (
              <div
                key={`${page}-${index}`}
                className={`mock-pdf-row ${index === 1 || index === 7 ? "highlight" : ""}`}
              >
                <span>{row[0]}</span>
                <span>{row[1]}</span>
                <span>{row[2]}</span>
                <span>{row[3]}</span>
              </div>
            ))}
          </div>
          <div className="mock-pdf-total">
            <span>Saldo final do período</span>
            <strong>R$ 38.548,73</strong>
          </div>
          <footer>
            <span>Documento demonstrativo · OFX Simples PRO</span>
            <span>
              Página {page} de {conversion.pdf.pageCount}
            </span>
          </footer>
        </div>
      </div>
    </section>
  );
}

function EditableCell({
  transaction,
  field,
  onCommit,
}: {
  transaction: Transaction;
  field: TransactionField;
  onCommit(
    transactionId: string,
    field: TransactionField,
    value: string,
  ): string | null;
}) {
  const rawValue = transaction[field];
  const formatted =
    typeof rawValue === "number" ? formatCurrency(rawValue) : (rawValue ?? "");
  const [value, setValue] = useState(String(formatted));
  const [previousFormatted, setPreviousFormatted] = useState(String(formatted));
  const [error, setError] = useState<string | null>(null);

  if (String(formatted) !== previousFormatted && String(formatted) !== value) {
    setPreviousFormatted(String(formatted));
    setValue(String(formatted));
  }

  const commit = (nextValue: string) => {
    const validationError = onCommit(transaction.id, field, nextValue);
    setError(validationError);
  };

  return (
    <div className="editable-wrap">
      <input
        className={`${transaction.fieldStates[field] === "SUSPECTED" ? "is-suspected" : ""} ${transaction.fieldStates[field] === "EDITED" ? "is-edited" : ""} ${error ? "is-invalid" : ""}`}
        value={value}
        onChange={(event) => {
          const nextValue = event.target.value;
          setValue(nextValue);
          if (field === "description") commit(nextValue);
        }}
        onBlur={() => field !== "description" && commit(value)}
        aria-invalid={Boolean(error)}
        title={error ?? undefined}
      />
      {transaction.fieldStates[field] === "SUSPECTED" && (
        <span className="field-dot" title="Campo suspeito" />
      )}
    </div>
  );
}

function TransactionGrid({
  conversion,
  onUpdate,
  onAdd,
  onRemove,
}: {
  conversion: Conversion;
  onUpdate(
    transactionId: string,
    field: TransactionField,
    value: string,
  ): string | null;
  onAdd(): void;
  onRemove(transactionId: string): void;
}) {
  const [filter, setFilter] = useState<TransactionFilter>("ALL");
  const visible = filterTransactions(conversion.transactions, filter);
  const suspected = conversion.transactions.filter((item) =>
    transactionHasState(item, "SUSPECTED"),
  ).length;
  const edited = conversion.transactions.filter((item) =>
    transactionHasState(item, "EDITED"),
  ).length;

  return (
    <section className="transactions-panel">
      <div className="panel-toolbar transactions-toolbar">
        <div className="toolbar-title">
          <span>Lançamentos</span>
          <span className="count-chip">{conversion.transactionCount}</span>
        </div>
        <button className="secondary-button small" onClick={onAdd}>
          <Plus size={14} /> Adicionar
        </button>
      </div>
      <div className="transaction-filters" aria-label="Filtros de lançamentos">
        <button
          className={filter === "ALL" ? "active" : ""}
          onClick={() => setFilter("ALL")}
        >
          Todos <span>{conversion.transactions.length}</span>
        </button>
        <button
          className={filter === "SUSPECTED" ? "active" : ""}
          onClick={() => setFilter("SUSPECTED")}
        >
          Suspeitos <span>{suspected}</span>
        </button>
        <button
          className={filter === "EDITED" ? "active" : ""}
          onClick={() => setFilter("EDITED")}
        >
          Alterados <span>{edited}</span>
        </button>
      </div>
      <div className="table-scroll">
        <table aria-label="Lançamentos extraídos">
          <thead>
            <tr>
              <th>Data</th>
              <th>Descrição</th>
              <th>Crédito</th>
              <th>Débito</th>
              <th>Saldo</th>
              <th>
                <span className="sr-only">Ações</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((transaction) => (
              <tr
                key={transaction.id}
                className={
                  transactionHasState(transaction, "SUSPECTED")
                    ? "suspected-row"
                    : ""
                }
              >
                <td>
                  <EditableCell
                    transaction={transaction}
                    field="date"
                    onCommit={onUpdate}
                  />
                </td>
                <td>
                  <EditableCell
                    transaction={transaction}
                    field="description"
                    onCommit={onUpdate}
                  />
                </td>
                <td>
                  <EditableCell
                    transaction={transaction}
                    field="credit"
                    onCommit={onUpdate}
                  />
                </td>
                <td>
                  <EditableCell
                    transaction={transaction}
                    field="debit"
                    onCommit={onUpdate}
                  />
                </td>
                <td>
                  <EditableCell
                    transaction={transaction}
                    field="balance"
                    onCommit={onUpdate}
                  />
                </td>
                <td>
                  <button
                    className="row-action"
                    aria-label={`Remover ${transaction.description}`}
                    onClick={() =>
                      window.confirm("Remover este lançamento?") &&
                      onRemove(transaction.id)
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {visible.length === 0 && (
          <div className="empty-table">Nenhum lançamento neste filtro.</div>
        )}
      </div>
      <footer className="grid-legend">
        <span>
          <i className="legend-suspect" /> Campo suspeito
        </span>
        <span>
          <i className="legend-edited" /> Campo alterado
        </span>
        <span>Edite diretamente nas células</span>
      </footer>
    </section>
  );
}

export function WorkspaceScreen({
  batchId,
  initialConversionId,
  repository = mockWorkspaceRepository,
}: WorkspaceScreenProps) {
  const controller = useWorkspaceController(
    batchId,
    repository,
    initialConversionId,
  );
  const [navigatorCollapsed, setNavigatorCollapsed] = useState(false);
  const [focusMode, setFocusMode] = useState(true);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        void controller.markReviewed();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        controller.undo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [controller]);

  if (controller.loadState === "LOADING") {
    return (
      <main className="screen-state">
        <LoaderCircle className="spin" size={30} />
        <h1>Abrindo workspace PRO</h1>
        <p>Preparando o lote e as conversões mockadas…</p>
      </main>
    );
  }
  if (controller.loadState === "ERROR" || !controller.batch) {
    return (
      <main className="screen-state error">
        <AlertCircle size={30} />
        <h1>Não foi possível abrir o lote</h1>
        <p>{controller.loadError}</p>
      </main>
    );
  }

  const { batch, activeConversion } = controller;
  const reviewed = batch.conversions.filter(
    (item) => item.status === "REVIEWED" || item.status === "EXPORTED",
  ).length;
  const pending = batch.conversions.filter(
    (item) => item.status === "NEEDS_REVIEW" || item.status === "READY",
  ).length;
  const progress = Math.round((reviewed / batch.conversions.length) * 100);
  const canReview = !["DIRTY", "SAVING", "ERROR"].includes(
    controller.saveStatus,
  );

  return (
    <main className={`workspace ${focusMode ? "focus-mode" : ""}`}>
      <ReviewRail />
      <header className="workspace-header">
        <div className="batch-heading">
          <Link href="/conversions" className="back-link">
            <ChevronLeft size={16} /> Lotes
          </Link>
          <div>
            <span className="eyebrow">{batch.clientName}</span>
            <h1>{batch.name}</h1>
          </div>
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
            onClick={() => setFocusMode((value) => !value)}
            title="Alternar modo de foco"
          >
            <PanelLeftClose size={17} />
          </button>
          <button
            className="secondary-button"
            onClick={() => void controller.exportBatch()}
            disabled={controller.exportState === "EXPORTING"}
          >
            <Download size={15} />{" "}
            {controller.exportState === "EXPORTING"
              ? "Preparando…"
              : "Exportar lote"}
          </button>
          <button
            className="primary-button"
            onClick={() => void controller.markReviewed()}
            disabled={
              !activeConversion ||
              !canReview ||
              activeConversion.status === "PROCESSING" ||
              activeConversion.status === "FAILED"
            }
          >
            <CheckCircle2 size={16} /> Marcar revisado
          </button>
        </div>
      </header>

      <div className="workspace-body">
        <ConversionNavigator
          conversions={batch.conversions}
          activeId={activeConversion?.id ?? ""}
          collapsed={navigatorCollapsed}
          onCollapse={() => setNavigatorCollapsed((value) => !value)}
          onSelect={controller.selectConversion}
          onNext={controller.goToNextPending}
        />
        {activeConversion ? (
          <div className="review-stage">
            <PdfViewer
              key={activeConversion.id}
              conversion={activeConversion}
            />
            {activeConversion.status === "FAILED" ? (
              <section className="transactions-panel viewer-state error">
                <RotateCcw size={28} />
                <h3>Conversão interrompida</h3>
                <p>
                  Tente processar este arquivo novamente sem bloquear o restante
                  do lote.
                </p>
                <button
                  className="primary-button"
                  onClick={() => void controller.retryConversion()}
                >
                  Tentar novamente
                </button>
              </section>
            ) : activeConversion.status === "PROCESSING" ? (
              <section className="transactions-panel viewer-state">
                <LoaderCircle className="spin" size={28} />
                <h3>Aguardando lançamentos</h3>
                <p>Os dados aparecerão quando o processamento terminar.</p>
                <button
                  className="secondary-button"
                  onClick={controller.goToNextPending}
                >
                  Revisar próximo arquivo
                </button>
              </section>
            ) : (
              <TransactionGrid
                conversion={activeConversion}
                onUpdate={controller.updateField}
                onAdd={controller.addTransaction}
                onRemove={controller.removeTransaction}
              />
            )}
          </div>
        ) : (
          <div className="viewer-state">
            <FileText size={28} />
            <h3>Selecione uma conversão</h3>
          </div>
        )}
      </div>

      <footer className="workspace-footer">
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
          disabled={!controller.canUndo}
        >
          <Undo2 size={14} /> Desfazer
        </button>
        <button className="footer-action" onClick={controller.goToNextPending}>
          Próxima pendente <Redo2 size={14} />
        </button>
      </footer>

      {controller.exportState === "DONE" && controller.exportResult && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          <div>
            <strong>Exportação simulada concluída</strong>
            <span>
              {controller.exportResult.fileName} ·{" "}
              {controller.exportResult.conversionCount} conversões
            </span>
          </div>
          <button
            onClick={() => window.location.reload()}
            aria-label="Fechar aviso"
          >
            ×
          </button>
        </div>
      )}
    </main>
  );
}
