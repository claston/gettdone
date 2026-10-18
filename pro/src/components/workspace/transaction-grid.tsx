"use client";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
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
import { sourceLocation } from "./source-location";

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
}

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
  onLocateTransaction(transactionId: string): void;
  locatedTransactionId?: string;
  activePage: number | null;
}) {
  const [filter, setFilter] = useState<TransactionFilter>("ALL");
  const [adding, setAdding] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const rowElements = useRef(new Map<string, HTMLTableRowElement>());
  const visible = filterTransactions(conversion.transactions, filter);
  const belongsToPage = (row: Transaction) =>
    row.sourcePage === activePage ||
    row.sourceRegions?.some((region) => region.page === activePage);
  const located = visible.find(
    (row) => row.id === locatedTransactionId && belongsToPage(row),
  );
  const targetId = located?.id ?? visible.find(belongsToPage)?.id;
  const follow = useCallback((id: string) => {
    const row = rowElements.current.get(id);
    const container = scroll.current;
    if (!row || !container) return;
    container.scrollTop +=
      row.getBoundingClientRect().top -
      container.getBoundingClientRect().top -
      32;
  }, []);
  const sync = usePageSync(activePage, targetId, follow);
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
    if (matchId && !sync.editing)
      rowElements.current
        .get(matchId)
        ?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [matchId, groupByPage, sync.editing]);
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
        onFocusCapture={(event) => {
          if (
            (event.target as HTMLElement).matches(
              "input:not([readonly]), textarea, [contenteditable=true]",
            )
          )
            sync.setEditing(true);
        }}
        onBlurCapture={(event) => {
          const next = event.relatedTarget as HTMLElement | null;
          if (
            !next ||
            !event.currentTarget.contains(next) ||
            !next.matches(
              "input:not([readonly]), textarea, [contenteditable=true]",
            )
          )
            sync.setEditing(false);
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
                      key={transaction.id}
                      ref={(node) => {
                        if (node) rowElements.current.set(transaction.id, node);
                        else rowElements.current.delete(transaction.id);
                      }}
                      className={`${transactionHasState(transaction, "SUSPECTED") ? "suspected-row" : ""} ${transaction.isDeleted ? "deleted-row" : ""} ${transaction.id === matchId ? "located-row" : ""}`}
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
                        {transaction.isDeleted && (
                          <span className="deleted-badge">Excluído</span>
                        )}
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
                        {location && conversion.pdf ? (
                          <button
                            className="row-action"
                            aria-label={`Localizar origem de ${transaction.description} no PDF (página ${location.page})`}
                            title={
                              location.regions.length
                                ? "Destacar origem no PDF"
                                : "Abrir página de origem no PDF"
                            }
                            onClick={() => onLocateTransaction(transaction.id)}
                          >
                            <ExternalLink size={14} />
                          </button>
                        ) : (
                          <span
                            className="source-unavailable"
                            title="Sem página de origem disponível"
                            aria-label="Sem página de origem disponível"
                          >
                            —
                          </span>
                        )}
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
