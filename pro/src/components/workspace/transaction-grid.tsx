"use client";
import {
  Fragment,
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Search, Plus, ExternalLink, RotateCcw, Trash2 } from "lucide-react";
import {
  filterTransactions,
  formatCurrency,
  transactionHasState,
} from "@/domain/workspace";
import type {
  Conversion,
  Transaction,
  TransactionField,
  TransactionDraft,
  TransactionFilter,
} from "@/domain/workspace";
import { TransactionDialog } from "./transaction-dialog";
import { usePageSync } from "./use-page-sync";
import {
  sourceLocation,
  transactionAtReadingPosition,
  transactionRegions,
  type PdfReadingPosition,
  type SourceSelectionOptions,
} from "./source-location";

const EditableCell = memo(function EditableCell({
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
        aria-label={`${{ date: "Data", description: "Descrição", credit: "Crédito", debit: "Débito", balance: "Saldo" }[field]} de ${transaction.description}`}
        readOnly={field === "balance" || transaction.isDeleted}
        className={`${transaction.fieldStates[field] === "SUSPECTED" ? "is-suspected" : ""} ${transaction.fieldStates[field] === "EDITED" ? "is-edited" : ""} ${error ? "is-invalid" : ""}`}
        value={value}
        onChange={(event) => {
          const nextValue = event.target.value;
          setValue(nextValue);
          if (field === "description") commit(nextValue);
        }}
        onBlur={() =>
          field !== "description" && field !== "balance" && commit(value)
        }
        aria-invalid={Boolean(error)}
        title={error ?? undefined}
      />
      {transaction.fieldStates[field] === "SUSPECTED" && (
        <span className="field-dot" title="Campo suspeito" />
      )}
    </div>
  );
});

export function TransactionGrid({
  conversion,
  onUpdate,
  onAdd,
  onRemove,
  onRestore,
  groupByPage,
  onGroupChange,
  onSourcePage,
  onLocateTransaction,
  locatedTransactionId,
  readingPosition,
  followPdf = true,
  onClearSource,
  filterValue,
  onFilterChange,
  onSelectTransaction,
  onEditingChange,
  syncPaused = false,
  selectedFromPdf = false,
  activePage,
}: {
  conversion: Conversion;
  onUpdate(
    transactionId: string,
    field: TransactionField,
    value: string,
  ): string | null;
  onAdd(draft: TransactionDraft): void;
  onRemove(transactionId: string): void;
  onRestore(transactionId: string): void;
  groupByPage: boolean;
  onGroupChange(value: boolean): void;
  onSourcePage(page: number): void;
  onLocateTransaction(
    transactionId: string,
    options?: SourceSelectionOptions,
  ): void;
  locatedTransactionId?: string;
  readingPosition?: PdfReadingPosition;
  followPdf?: boolean;
  onClearSource?(): void;
  filterValue?: TransactionFilter;
  onFilterChange?(value: TransactionFilter): void;
  onSelectTransaction?(id: string, editing?: boolean): void;
  onEditingChange?(editing: boolean): void;
  syncPaused?: boolean;
  selectedFromPdf?: boolean;
  activePage: number | null;
}) {
  const updateRef = useRef(onUpdate);
  useLayoutEffect(() => {
    updateRef.current = onUpdate;
  }, [onUpdate]);
  const commitField = useCallback(
    (...args: Parameters<typeof onUpdate>) => updateRef.current(...args),
    [],
  );
  const [localFilter, setLocalFilter] = useState<TransactionFilter>("ALL");
  const filter = filterValue ?? localFilter;
  const setFilter = (value: TransactionFilter) =>
    onFilterChange ? onFilterChange(value) : setLocalFilter(value);
  const [adding, setAdding] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const rowElements = useRef(new Map<string, HTMLTableRowElement>());
  const visible = useMemo(
    () => filterTransactions(conversion.transactions, filter),
    [conversion.transactions, filter],
  );
  const userScrolling = useRef(false);
  const editing = useRef(false);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollFrame = useRef<number | null>(null);
  const pointer = useRef<{ x: number; y: number; id: string } | null>(null);
  const cancelHover = useCallback(() => {
    if (hoverTimer.current !== null) clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  }, []);
  useEffect(() => {
    cancelHover();
    pointer.current = null;
    userScrolling.current = false;
  }, [visible, groupByPage, cancelHover, syncPaused]);
  useEffect(() => {
    if (
      locatedTransactionId &&
      !visible.some((row) => row.id === locatedTransactionId)
    )
      onClearSource?.();
  }, [visible, locatedTransactionId, onClearSource]);
  useEffect(
    () => () => {
      cancelHover();
      if (scrollFrame.current !== null)
        cancelAnimationFrame(scrollFrame.current);
    },
    [cancelHover],
  );
  useEffect(() => {
    if (!scroll.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      userScrolling.current = false;
      cancelHover();
    });
    observer.observe(scroll.current);
    return () => observer.disconnect();
  }, [cancelHover]);
  const belongsToPage = (row: Transaction) =>
    row.sourcePage === activePage ||
    transactionRegions(row).some((region) => region.page === activePage);
  const located = visible.find(
    (row) =>
      row.id === locatedTransactionId &&
      (selectedFromPdf || belongsToPage(row)),
  );
  const readingTarget =
    !selectedFromPdf && readingPosition?.page === activePage
      ? transactionAtReadingPosition(visible, readingPosition)
      : undefined;
  const targetId =
    (followPdf ? readingTarget?.id : undefined) ??
    located?.id ??
    visible.find(belongsToPage)?.id;
  const follow = useCallback((id: string) => {
    const row = rowElements.current.get(id);
    const container = scroll.current;
    if (!row || !container) return;
    userScrolling.current = false;
    container.scrollTop +=
      row.getBoundingClientRect().top -
      container.getBoundingClientRect().top -
      32;
  }, []);
  const sync = usePageSync(
    activePage,
    targetId,
    follow,
    followPdf && !syncPaused,
  );
  useEffect(() => {
    if (
      followPdf &&
      !syncPaused &&
      !sync.editing &&
      readingTarget &&
      readingTarget.id !== locatedTransactionId
    )
      onLocateTransaction(readingTarget.id, { origin: "pdf", scroll: false });
  }, [
    followPdf,
    syncPaused,
    sync.editing,
    readingTarget,
    locatedTransactionId,
    onLocateTransaction,
  ]);
  useEffect(() => {
    if (
      followPdf &&
      !syncPaused &&
      !sync.editing &&
      readingPosition?.page === activePage &&
      !readingTarget &&
      locatedTransactionId
    )
      onClearSource?.();
  }, [
    followPdf,
    syncPaused,
    sync.editing,
    readingPosition,
    activePage,
    readingTarget,
    locatedTransactionId,
    onClearSource,
  ]);
  const startUserScroll = () => {
    cancelHover();
    userScrolling.current = true;
  };
  const onGridScroll = () => {
    if (
      !userScrolling.current ||
      editing.current ||
      syncPaused ||
      activePage === null ||
      scrollFrame.current !== null
    )
      return;
    scrollFrame.current = requestAnimationFrame(() => {
      scrollFrame.current = null;
      const container = scroll.current;
      if (!container || !userScrolling.current || editing.current) return;
      const bounds = container.getBoundingClientRect();
      const bottom =
        container.scrollTop > 0 &&
        container.scrollTop + container.clientHeight >=
          container.scrollHeight - 1;
      const line = bottom
        ? bounds.bottom
        : bounds.top + Math.min(120, container.clientHeight * 0.2);
      let selected: string | undefined;
      let distance = Infinity;
      for (const row of visible) {
        const element = rowElements.current.get(row.id);
        if (!element || !sourceLocation(row)) continue;
        const rect = element.getBoundingClientRect();
        if (rect.bottom < bounds.top || rect.top > bounds.bottom) continue;
        const candidate = Math.abs(rect.top + rect.height / 2 - line);
        if (candidate < distance) {
          distance = candidate;
          selected = row.id;
        }
      }
      if (selected && selected !== locatedTransactionId)
        onLocateTransaction(selected, { origin: "grid", scroll: true });
    });
  };
  const hasPageRows = conversion.transactions.some(belongsToPage);
  const groups = groupByPage
    ? Array.from(new Set(visible.map((row) => row.sourcePage ?? null)))
        .sort((a, b) => (a ?? Infinity) - (b ?? Infinity))
        .map((page) => ({
          page,
          rows: visible.filter((row) => (row.sourcePage ?? null) === page),
        }))
    : [{ page: null, rows: visible }];
  const normalize = (value: string) =>
    value
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("pt-BR");
  const search = normalize(query.trim());
  const matchId = search
    ? groups
        .flatMap((group) => group.rows)
        .find((row) => normalize(row.description).includes(search))?.id
    : undefined;
  useEffect(() => {
    if (matchId && !sync.editing) {
      userScrolling.current = false;
      rowElements.current
        .get(matchId)
        ?.scrollIntoView?.({ block: "center", behavior: "smooth" });
    }
  }, [matchId, groupByPage, sync.editing]);
  const suspected = conversion.transactions.filter((item) =>
    transactionHasState(item, "SUSPECTED"),
  ).length;
  const edited = conversion.transactions.filter((item) =>
    transactionHasState(item, "EDITED"),
  ).length;

  return (
    <section className="transactions-panel">
      {conversion.partialStatement && (
        <div className="page-sync-notice" role="status">
          Extrato parcial: o arquivo pode ter páginas ausentes ou um limite de
          consulta do banco. Os movimentos disponíveis foram convertidos.
          Confira o período e os saldos; se o banco informar limite de consulta,
          baixe o extrato consolidado completo.
        </div>
      )}
      <div className="panel-toolbar transactions-toolbar">
        <div className="toolbar-title">
          <span>Lançamentos</span>
          <span className="count-chip">{conversion.transactionCount}</span>
        </div>
        <select
          aria-label="Organizar lançamentos"
          value={groupByPage ? "PAGES" : "CONTINUOUS"}
          onChange={(event) => onGroupChange(event.target.value === "PAGES")}
        >
          <option value="CONTINUOUS">Lista contínua</option>
          <option value="PAGES">Separar por páginas</option>
        </select>
        <label className="transaction-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            aria-label="Buscar lançamentos"
            placeholder="Buscar texto"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span className="sr-only" role="status">
            {search
              ? matchId
                ? "Primeira ocorrência localizada"
                : "Nenhuma ocorrência neste filtro"
              : ""}
          </span>
        </label>
        <button
          className="secondary-button small"
          onClick={() => setAdding(true)}
        >
          <Plus size={14} /> Adicionar
        </button>
      </div>
      {activePage !== null && !targetId && !sync.editing && (
        <div className="page-sync-notice" role="status">
          {hasPageRows
            ? `Página ${activePage}: nenhum lançamento visível neste filtro`
            : `Página ${activePage}: sem lançamentos associados`}
        </div>
      )}
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
      <div
        className="table-scroll"
        ref={scroll}
        onWheel={startUserScroll}
        onTouchStart={startUserScroll}
        onPointerDown={(event) => {
          cancelHover();
          // Clicking a cell/action is not a scrolling gesture. Only direct
          // scrollbar interaction and touch may claim scroll ownership here.
          userScrolling.current = false;
          if (
            event.pointerType === "touch" ||
            event.target === event.currentTarget
          )
            startUserScroll();
        }}
        onScroll={onGridScroll}
        onPointerLeave={() => {
          cancelHover();
          pointer.current = null;
        }}
        onKeyDownCapture={(event) => {
          if (event.altKey && ["ArrowDown", "ArrowUp"].includes(event.key)) {
            const currentRow = (
              event.target as HTMLElement
            ).closest<HTMLTableRowElement>("tr[data-transaction-id]");
            const currentIndex = visible.findIndex(
              (row) => row.id === currentRow?.dataset.transactionId,
            );
            const next =
              visible[currentIndex + (event.key === "ArrowDown" ? 1 : -1)];
            if (currentIndex >= 0 && next) {
              event.preventDefault();
              const inputs = Array.from(currentRow!.querySelectorAll("input"));
              const field = Math.max(
                0,
                inputs.indexOf(event.target as HTMLInputElement),
              );
              const target = rowElements.current
                .get(next.id)
                ?.querySelectorAll<HTMLInputElement>("input")[field];
              target?.focus();
              target?.scrollIntoView({ block: "nearest" });
            }
            return;
          }
          if (
            !editing.current &&
            [
              "ArrowDown",
              "ArrowUp",
              "PageDown",
              "PageUp",
              "Home",
              "End",
              " ",
            ].includes(event.key)
          )
            startUserScroll();
        }}
        onFocusCapture={(event) => {
          const id = (event.target as HTMLElement).closest<HTMLTableRowElement>(
            "tr[data-transaction-id]",
          )?.dataset.transactionId;
          if (
            id &&
            (event.target as HTMLElement).matches(
              "input, textarea, [contenteditable=true]",
            )
          )
            if (id !== locatedTransactionId) onSelectTransaction?.(id, true);
          if (
            (event.target as HTMLElement).matches(
              "input:not([readonly]), textarea, [contenteditable=true]",
            )
          ) {
            editing.current = true;
            cancelHover();
            sync.setEditing(true);
            onEditingChange?.(true);
          }
        }}
        onBlurCapture={(event) => {
          const next = event.relatedTarget as HTMLElement | null;
          if (
            !next ||
            !event.currentTarget.contains(next) ||
            !next.matches(
              "input:not([readonly]), textarea, [contenteditable=true]",
            )
          ) {
            editing.current = false;
            sync.setEditing(false);
            onEditingChange?.(false);
          }
        }}
      >
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
            {groups.map((group) => (
              <Fragment key={group.page ?? "unknown"}>
                {groupByPage && (
                  <tr className="source-page-heading">
                    <td colSpan={6}>
                      {group.page === null ? (
                        "Sem página de origem · lançamentos manuais ou arquivos anteriores"
                      ) : (
                        <button
                          onClick={() => onSourcePage(group.page!)}
                          aria-label={`Ver página ${group.page} no PDF`}
                        >
                          Página {group.page} <ExternalLink size={12} />
                        </button>
                      )}
                      <span> · {group.rows.length} lanç.</span>
                    </td>
                  </tr>
                )}
                {group.rows.map((transaction) => {
                  const location = sourceLocation(transaction);
                  return (
                    <tr
                      data-transaction-id={transaction.id}
                      data-source-page={transaction.sourcePage ?? undefined}
                      onClick={(event) => {
                        if (
                          !syncPaused &&
                          (event.target === event.currentTarget ||
                            (event.target as HTMLElement).tagName === "TD")
                        )
                          onSelectTransaction?.(transaction.id);
                      }}
                      aria-current={
                        transaction.id === locatedTransactionId
                          ? "true"
                          : undefined
                      }
                      key={transaction.id}
                      ref={(node) => {
                        if (node) rowElements.current.set(transaction.id, node);
                        else rowElements.current.delete(transaction.id);
                      }}
                      className={`${transactionHasState(transaction, "SUSPECTED") ? "suspected-row" : ""} ${transaction.isDeleted ? "deleted-row" : ""} ${transaction.id === matchId ? "located-row" : ""} ${transaction.id === locatedTransactionId ? "source-active-row" : ""}`}
                      onPointerMove={(event) => {
                        if (
                          event.pointerType === "touch" ||
                          editing.current ||
                          syncPaused ||
                          activePage === null ||
                          !location
                        )
                          return;
                        const previous = pointer.current;
                        if (
                          previous &&
                          (previous.id === transaction.id ||
                            Math.hypot(
                              previous.x - event.clientX,
                              previous.y - event.clientY,
                            ) < 2)
                        )
                          return;
                        pointer.current = {
                          id: transaction.id,
                          x: event.clientX,
                          y: event.clientY,
                        };
                        cancelHover();
                        hoverTimer.current = setTimeout(() => {
                          if (
                            !editing.current &&
                            transaction.id !== locatedTransactionId
                          )
                            onLocateTransaction(transaction.id, {
                              origin: "grid",
                              scroll: true,
                            });
                        }, 110);
                      }}
                      onPointerLeave={() => {
                        cancelHover();
                        pointer.current = null;
                      }}
                    >
                      <td>
                        <EditableCell
                          transaction={transaction}
                          field="date"
                          onCommit={commitField}
                        />
                      </td>
                      <td>
                        <EditableCell
                          transaction={transaction}
                          field="description"
                          onCommit={commitField}
                        />
                        {transaction.isDeleted && (
                          <span className="deleted-badge">Excluído</span>
                        )}
                      </td>
                      <td>
                        <EditableCell
                          transaction={transaction}
                          field="credit"
                          onCommit={commitField}
                        />
                      </td>
                      <td>
                        <EditableCell
                          transaction={transaction}
                          field="debit"
                          onCommit={commitField}
                        />
                      </td>
                      <td>
                        <EditableCell
                          transaction={transaction}
                          field="balance"
                          onCommit={commitField}
                        />
                      </td>
                      <td>
                        {transaction.isDeleted ? (
                          <button
                            className="row-action"
                            aria-label={`Restaurar ${transaction.description}`}
                            onClick={() => onRestore(transaction.id)}
                          >
                            <RotateCcw size={14} />
                          </button>
                        ) : (
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
                        )}
                      </td>
                    </tr>
                  );
                })}
              </Fragment>
            ))}
          </tbody>
        </table>
        {visible.length === 0 && (
          <div className="empty-table" role="status">
            {conversion.noActivity && conversion.transactions.length === 0 ? (
              <>
                <p>Extrato sem movimentação no período.</p>
                <p>
                  A conversão foi concluída. Você pode baixar o OFX sem
                  lançamentos.
                </p>
              </>
            ) : (
              "Nenhum lançamento neste filtro."
            )}
          </div>
        )}
      </div>
      <footer className="grid-legend">
        <span>
          <i className="legend-suspect" /> Campo suspeito
        </span>
        <span>
          <i className="legend-edited" /> Campo alterado
        </span>
        <span>
          {search
            ? matchId
              ? "Primeira ocorrência"
              : "Nenhuma ocorrência neste filtro"
            : "Edite diretamente nas células"}
        </span>
      </footer>
      {adding && (
        <TransactionDialog
          initialDate={
            conversion.transactions[0]?.date ??
            new Date().toLocaleDateString("pt-BR")
          }
          onClose={() => setAdding(false)}
          onAdd={(draft) => {
            setFilter("ALL");
            onGroupChange(false);
            onAdd(draft);
            if (scroll.current) scroll.current.scrollTop = 0;
          }}
        />
      )}
    </section>
  );
}
