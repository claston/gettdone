"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import {
  receivedService,
  type ReceivedService,
  type ReceivedPage,
  type ReceivedClient,
  type PendingStatement,
} from "@/data/received-statements-service";
import styles from "./received-statements.module.css";
import { IdentityDocumentPreview } from "./identity-document-preview";

const banks: Record<string, string> = {
  "001": "Banco do Brasil",
  "033": "Santander",
  "077": "Inter",
  "104": "Caixa",
  "237": "Bradesco",
  "260": "Nubank",
  "341": "Itaú",
  "748": "Sicredi",
  "756": "Sicoob",
};
type ClientTab = "active" | "clients" | "pending";
const tabOrder: ClientTab[] = ["active", "clients", "pending"];
function taxLabel(value: string | null) {
  if (!value) return "CPF/CNPJ a confirmar";
  return value.length === 14
    ? `CNPJ ${value.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5")}`
    : `CPF ${value.replace(/^(\d{3})(\d{3})(\d{3})(\d{2})$/, "$1.$2.$3-$4")}`;
}

function pendingReason(source: PendingStatement["source"]) {
  if (source.identity_issues?.includes("unreadable_holder_tax_id"))
    return "CPF/CNPJ do titular ilegível. Confira a identificação no documento.";
  if (source.confidence === "ambiguous")
    return "Identificação ambígua. Confirme o titular no documento.";
  return "Titular sem evidência suficiente para associação automática.";
}

function IdentityForm({
  client,
  pending,
  clients,
  service,
  onSaved,
  onCancel,
}: {
  client?: ReceivedClient;
  pending?: PendingStatement;
  clients: ReceivedClient[];
  service: ReceivedService;
  onSaved(): void;
  onCancel(): void;
}) {
  const [name, setName] = useState(
    client?.name ?? pending?.source.holder_name ?? "",
  );
  const [tax, setTax] = useState(
    client?.tax_id ?? pending?.source.holder_tax_id ?? "",
  );
  const [clientId, setClientId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        const operation = client
          ? service.edit(client.id, {
              name,
              tax_id: tax || null,
              expected_updated_at: client.updated_at,
            })
          : service.confirm(pending!.job_id, {
              name,
              tax_id: tax || null,
              expected_version: pending!.version,
              client_id: clientId || null,
            });
        void operation
          .then(onSaved)
          .catch((caught: unknown) =>
            setError(
              caught instanceof Error
                ? caught.message
                : "Não foi possível salvar a identificação.",
            ),
          )
          .finally(() => setBusy(false));
      }}
    >
      {pending && (
        <>
          <p>
            Confira a identidade do titular no documento antes de confirmar.
          </p>
          {pending.source.references?.length ? (
            <p className={styles.muted}>
              Evidência no documento: páginas{" "}
              {[
                ...new Set(pending.source.references.map((ref) => ref.page)),
              ].join(", ")}
              .
            </p>
          ) : (
            <p className={styles.muted}>
              Titular sem evidência suficiente para associação automática.
            </p>
          )}
          {clients.length > 0 && (
            <label>
              Associar a cliente identificado
              <select
                value={clientId}
                onChange={(event) => {
                  setClientId(event.target.value);
                  const selected = clients.find(
                    (item) => item.id === event.target.value,
                  );
                  if (selected) {
                    setName(selected.name);
                    setTax(selected.tax_id ?? "");
                  } else {
                    setName(pending.source.holder_name ?? "");
                    setTax(pending.source.holder_tax_id ?? "");
                  }
                }}
              >
                <option value="">Confirmar os dados deste documento</option>
                {clients.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} — {taxLabel(item.tax_id)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </>
      )}
      <label>
        Nome do cliente
        <input
          required
          disabled={Boolean(pending && clientId)}
          maxLength={160}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        CPF/CNPJ do titular
        <input
          maxLength={30}
          disabled={Boolean(pending && clientId)}
          value={tax}
          onChange={(event) => setTax(event.target.value)}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <div className={styles.actions}>
        <button className="primary-button" disabled={busy}>
          {busy ? "Salvando…" : "Salvar identificação"}
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={onCancel}
        >
          Cancelar
        </button>
      </div>
    </form>
  );
}

export function ReceivedStatementsPage({
  service = receivedService,
  financialDemoVisible = false,
  financialCenterVisible = false,
  financialDemo = false,
}: {
  service?: ReceivedService;
  financialDemoVisible?: boolean;
  financialCenterVisible?: boolean;
  financialDemo?: boolean;
}) {
  const [page, setPage] = useState<ReceivedPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | undefined>();
  const [activeCursor, setActiveCursor] = useState<string | undefined>();
  const [pendingCursor, setPendingCursor] = useState<string | undefined>();
  const [attempt, setAttempt] = useState(0);
  const [editing, setEditing] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(true);
  const [activeTab, setActiveTab] = useState<ClientTab>("active");
  const activeOnly = activeTab === "active";
  const selectedCursor = activeOnly ? activeCursor : cursor;
  const setSelectedCursor = activeOnly ? setActiveCursor : setCursor;
  const [loadedActiveOnly, setLoadedActiveOnly] = useState(true);
  const [refreshAt, setRefreshAt] = useState<number | null>(null);
  const [expiredSnapshot, setExpiredSnapshot] = useState(false);
  const [reload, setReload] = useState(0);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const synchronized = useRef<{
    service: ReceivedService;
    attempt: number;
  } | null>(null);
  const synchronization = useRef<{
    controller: AbortController;
    promise: Promise<void>;
  } | null>(null);
  useEffect(() => {
    return () => {
      synchronization.current?.controller.abort();
      synchronization.current = null;
    };
  }, [service, attempt]);
  useEffect(() => {
    if (search.trim() === query) return;
    const timer = setTimeout(() => {
      setCursor(undefined);
      setActiveCursor(undefined);
      setEditing(null);
      setQuery(search.trim());
    }, 250);
    return () => clearTimeout(timer);
  }, [search, query]);
  const tabId = useId();
  const clientTab = useRef<HTMLButtonElement>(null);
  const activeClientTab = useRef<HTMLButtonElement>(null);
  const pendingTab = useRef<HTMLButtonElement>(null);
  const selectTab = (tab: ClientTab, focus = false) => {
    if (tab !== activeTab) setEditing(null);
    setActiveTab(tab);
    if (focus)
      ({ active: activeClientTab, clients: clientTab, pending: pendingTab })[
        tab
      ].current?.focus();
  };
  useEffect(() => {
    if (refreshAt === null) return;
    const timer = setTimeout(
      () => {
        setExpiredSnapshot(true);
        setCursor(undefined);
        setActiveCursor(undefined);
        setPendingCursor(undefined);
        setReload((value) => value + 1);
      },
      Math.min(2_147_483_647, Math.max(0, refreshAt - Date.now())),
    );
    return () => clearTimeout(timer);
  }, [refreshAt]);
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible")
        setReload((value) => value + 1);
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    function acceptPage(value: ReceivedPage) {
      setPage(value);
      setLoadedActiveOnly(activeOnly);
      setExpiredSnapshot(false);
      const remaining =
        value.next_expiration_at && value.server_time
          ? Date.parse(value.next_expiration_at) - Date.parse(value.server_time)
          : null;
      setRefreshAt(
        remaining !== null && Number.isFinite(remaining)
          ? Date.now() + Math.max(0, remaining) + 100
          : null,
      );
    }
    async function load() {
      setError(null);
      setRefreshing(true);
      try {
        const first = await service.list(
          selectedCursor,
          abort.signal,
          pendingCursor,
          query,
          activeOnly,
        );
        if (abort.signal.aborted) return;
        acceptPage(first);
        if (
          synchronized.current?.service === service &&
          synchronized.current.attempt === attempt
        )
          return;
        // Changing the list filter must not restart an ingestion already in flight.
        if (!synchronization.current) {
          const controller = new AbortController();
          synchronization.current = {
            controller,
            promise: service.refresh(controller.signal),
          };
        }
        await synchronization.current.promise;
        if (abort.signal.aborted) return;
        synchronized.current = { service, attempt };
        const updated = await service.list(
          selectedCursor,
          abort.signal,
          pendingCursor,
          query,
          activeOnly,
        );
        if (!abort.signal.aborted) acceptPage(updated);
      } catch (caught) {
        if (!abort.signal.aborted)
          setError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível carregar os extratos.",
          );
      } finally {
        if (!abort.signal.aborted) setRefreshing(false);
      }
    }
    void load();
    return () => abort.abort();
  }, [
    service,
    selectedCursor,
    pendingCursor,
    attempt,
    query,
    activeOnly,
    reload,
  ]);
  const saved = () => {
    setEditing(null);
    setPendingCursor(undefined);
    setAttempt((value) => value + 1);
  };
  const scopeReady = loadedActiveOnly === activeOnly && !expiredSnapshot;
  const clients = (scopeReady ? [...(page?.clients ?? [])] : [])
    .filter((client) => !activeOnly || client.available_count > 0)
    .sort(
      (a, b) =>
        a.name.localeCompare(b.name, "pt-BR", { sensitivity: "base" }) ||
        a.id.localeCompare(b.id),
    );
  return (
    <section className={`standard-page ${styles.page}`}>
      <div className="page-title">
        <div>
          <h1>Clientes</h1>
          <p>
            Consulte os extratos, contas bancárias e pendências dos seus
            clientes.
          </p>
        </div>
        <div className={styles.actions}>
          <button
            className="secondary-button"
            disabled={refreshing}
            onClick={() => setAttempt((value) => value + 1)}
          >
            {refreshing && (
              <LoaderCircle
                size={16}
                className={styles.spinner}
                aria-hidden="true"
              />
            )}
            {refreshing ? "Atualizando extratos..." : "Atualizar extratos"}
          </button>
          <Link href="/conversions" className="primary-button">
            Nova conversão
          </Link>
        </div>
      </div>
      {financialDemo && (
        <p className={styles.muted}>
          Demonstração com clientes e documentos fictícios. Selecione um cliente
          para explorar a Central Financeira.{" "}
          <Link href="/demo/financial/trial">
            Ver jornada da primeira conversão →
          </Link>
        </p>
      )}
      {financialDemoVisible && !financialDemo && (
        <p>
          <Link className="secondary-button" href="/demo/financial/clients">
            Explorar Central Financeira · dados fictícios
          </Link>
        </p>
      )}
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button
            className="secondary-button"
            disabled={refreshing}
            onClick={() => setAttempt((value) => value + 1)}
          >
            Tentar novamente
          </button>
        </div>
      )}
      {!page ? (
        !error && (
          <div className={styles.loadingState}>
            <p className={styles.loadingMessage} role="status">
              <LoaderCircle
                size={22}
                className={styles.spinner}
                aria-hidden="true"
              />
              Carregando extratos...
            </p>
            <div className={styles.kpis} aria-hidden="true">
              {[0, 1, 2].map((item) => (
                <div className={styles.skeleton} key={item} />
              ))}
            </div>
            <div className={styles.clients} aria-hidden="true">
              {[0, 1].map((item) => (
                <div
                  className={`${styles.skeleton} ${styles.skeletonClient}`}
                  key={item}
                />
              ))}
            </div>
          </div>
        )
      ) : (
        <>
          <div className={styles.kpis} aria-label="Resumo dos extratos">
            {[
              ["Extratos", page.totals.statements],
              ["Prontos", page.totals.ready],
              ["Precisam de atenção", page.totals.attention],
            ].map(([label, total]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{total}</strong>
              </div>
            ))}
          </div>
          {refreshing && (
            <p className={styles.muted} role="status">
              Atualizando extratos...
            </p>
          )}
          <div
            className={styles.tabs}
            role="tablist"
            aria-label="Organização dos extratos"
          >
            {(
              [
                ["active", "Com conversões ativas", activeClientTab],
                ["clients", "Identificados", clientTab],
                ["pending", "Não identificados", pendingTab],
              ] as const
            ).map(([tab, label, ref]) => (
              <button
                type="button"
                key={tab}
                ref={ref}
                role="tab"
                id={`${tabId}-${tab}-tab`}
                aria-selected={activeTab === tab}
                aria-controls={`${tabId}-${tab}-panel`}
                tabIndex={activeTab === tab ? 0 : -1}
                onClick={() => selectTab(tab)}
                onKeyDown={(event) => {
                  if (
                    ["ArrowRight", "ArrowLeft", "Home", "End"].includes(
                      event.key,
                    )
                  ) {
                    event.preventDefault();
                    selectTab(
                      event.key === "Home"
                        ? "active"
                        : event.key === "End"
                          ? "pending"
                          : tabOrder[
                              (tabOrder.indexOf(tab) +
                                (event.key === "ArrowRight" ? 1 : 2)) %
                                3
                            ],
                      true,
                    );
                  }
                }}
              >
                {label} (
                {tab === "active"
                  ? (page.active_client_count ??
                    page.clients.filter((c) => c.available_count > 0).length)
                  : tab === "clients"
                    ? (page.all_client_count ??
                      page.client_count ??
                      page.clients.length)
                    : (page.pending_count ?? page.pending.length)}
                )
              </button>
            ))}
          </div>
          {tabOrder.map((tab) => (
            <div
              key={tab}
              role="tabpanel"
              id={`${tabId}-${tab}-panel`}
              aria-labelledby={`${tabId}-${tab}-tab`}
              aria-busy={refreshing}
              hidden={activeTab !== tab}
              tabIndex={0}
              className={styles.tabPanel}
            >
              {activeTab === tab &&
                (tab !== "pending" ? (
                  <>
                    <div
                      className={styles.search}
                      role="search"
                      aria-label="Buscar clientes"
                    >
                      <label htmlFor={`${tabId}-search`}>
                        Buscar por nome, CPF ou CNPJ
                      </label>
                      <div className={styles.actions}>
                        <input
                          id={`${tabId}-search`}
                          type="search"
                          value={search}
                          placeholder="Buscar por nome, CPF ou CNPJ"
                          maxLength={160}
                          autoComplete="off"
                          onChange={(event) => setSearch(event.target.value)}
                        />
                        {search && (
                          <button
                            className="secondary-button"
                            onClick={() => setSearch("")}
                          >
                            Limpar busca
                          </button>
                        )}
                      </div>
                    </div>
                    <div className={styles.clients}>
                      {clients.map((client) => (
                        <article className={styles.client} key={client.id}>
                          <div className={styles.clientHeader}>
                            <div>
                              {client.available_count || financialDemo ? (
                                <Link
                                  className={styles.clientLink}
                                  href={
                                    financialDemo
                                      ? `/demo/financial/${client.id}`
                                      : `/client-review/${client.id}`
                                  }
                                >
                                  <strong>{client.name}</strong>
                                  <span>{taxLabel(client.tax_id)}</span>
                                </Link>
                              ) : (
                                <div className={styles.clientLink}>
                                  <strong>{client.name}</strong>
                                  <span>{taxLabel(client.tax_id)}</span>
                                </div>
                              )}
                            </div>
                            <div className={styles.summary}>
                              <span
                                className={
                                  client.attention_count
                                    ? styles.attention
                                    : styles.ready
                                }
                              >
                                {!client.available_count
                                  ? "Nenhuma conversão ativa"
                                  : client.attention_count
                                    ? `${client.attention_count} ${client.attention_count === 1 ? "precisa" : "precisam"} de atenção`
                                    : client.processing_count
                                      ? `${client.processing_count} em processamento`
                                      : "Todos prontos"}
                              </span>
                              <span>
                                {client.available_count}{" "}
                                {client.available_count === 1
                                  ? "disponível"
                                  : "disponíveis"}{" "}
                                / {client.historical_count}{" "}
                                {client.historical_count === 1
                                  ? "extrato"
                                  : "extratos"}{" "}
                                no histórico
                              </span>
                              {client.expired_count > 0 && (
                                <small>
                                  {client.expired_count}{" "}
                                  {client.expired_count === 1
                                    ? "expirado"
                                    : "expirados"}
                                </small>
                              )}
                            </div>
                            {!financialDemo && (
                              <button
                                className="secondary-button small"
                                aria-label={`Editar ${client.name}`}
                                onClick={() => setEditing(client.id)}
                              >
                                Editar
                              </button>
                            )}
                            {financialDemo && (
                              <Link
                                className="secondary-button small"
                                href={`/demo/financial/${client.id}`}
                              >
                                Central Financeira
                              </Link>
                            )}
                            {financialCenterVisible && !financialDemo && (
                              <Link
                                className="secondary-button small"
                                href={`/financial/${client.id}`}
                              >
                                Central Financeira
                              </Link>
                            )}
                          </div>
                          {client.accounts.length > 0 && (
                            <ul className={styles.accounts}>
                              {client.accounts.map((account) => (
                                <li key={account.id}>
                                  {banks[account.bank_code] ??
                                    `Banco ${account.bank_code}`}{" "}
                                  · Conta {account.account_number}
                                  <span>
                                    {account.statement_count}{" "}
                                    {account.statement_count === 1
                                      ? "extrato"
                                      : "extratos"}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                          {editing === client.id && (
                            <IdentityForm
                              client={client}
                              clients={clients}
                              service={service}
                              onSaved={saved}
                              onCancel={() => setEditing(null)}
                            />
                          )}
                        </article>
                      ))}
                    </div>
                    {!scopeReady && (
                      <p role="status">Atualizando clientes...</p>
                    )}
                    {scopeReady && !clients.length && (
                      <p role="status">
                        {query
                          ? "Nenhum cliente encontrado"
                          : activeOnly
                            ? "Nenhum cliente com conversões ativas no momento."
                            : "Seus clientes aparecerão aqui conforme os titulares forem identificados nos documentos recebidos."}
                      </p>
                    )}
                    {scopeReady && (selectedCursor || page.next_cursor) && (
                      <nav
                        className={styles.actions}
                        aria-label="Paginação de clientes"
                      >
                        <button
                          className="secondary-button"
                          disabled={refreshing || !selectedCursor}
                          onClick={() => setSelectedCursor(undefined)}
                        >
                          Início
                        </button>
                        <button
                          className="secondary-button"
                          disabled={refreshing || !page.next_cursor}
                          onClick={() => setSelectedCursor(page.next_cursor!)}
                        >
                          Próxima
                        </button>
                      </nav>
                    )}
                  </>
                ) : (
                  <section className={styles.pending}>
                    <h2>Identificação pendente</h2>
                    <p>
                      {page.pending.length > 0 ||
                      pendingCursor ||
                      page.pending_next_cursor
                        ? "Estes extratos precisam de identificação do cliente. Os arquivos continuam disponíveis para revisão no lote."
                        : "Todos os extratos estão identificados."}
                    </p>
                    {page.pending.map((item) => (
                      <article key={item.job_id}>
                        <div className={styles.clientHeader}>
                          <Link
                            href={`/review/${item.batch_id}/${item.job_id}?return_to=%2Fdashboard`}
                            aria-label={`Conferir ${item.filename}`}
                          >
                            {item.filename}
                          </Link>
                          <button
                            className="secondary-button small"
                            onClick={() => setEditing(item.job_id)}
                          >
                            Conferir identificação
                          </button>
                        </div>
                        <div className={styles.pendingDetails}>
                          {(item.source.bank_code ||
                            item.source.account_number) && (
                            <p>
                              {item.source.bank_code &&
                                (banks[item.source.bank_code] ??
                                  `Banco ${item.source.bank_code}`)}
                              {item.source.branch &&
                                ` · Agência ${item.source.branch}`}
                              {item.source.account_number &&
                                ` · Conta ••${item.source.account_number.slice(-4)}`}
                            </p>
                          )}
                          <p>{pendingReason(item.source)}</p>
                        </div>
                        {editing === item.job_id && (
                          <div className={styles.identityReview}>
                            <IdentityDocumentPreview document={item} />
                            <IdentityForm
                              pending={item}
                              clients={clients}
                              service={service}
                              onSaved={saved}
                              onCancel={() => setEditing(null)}
                            />
                          </div>
                        )}
                      </article>
                    ))}
                    {(pendingCursor || page.pending_next_cursor) && (
                      <nav
                        className={styles.actions}
                        aria-label="Paginação de identificações"
                      >
                        <button
                          className="secondary-button"
                          disabled={refreshing || !pendingCursor}
                          onClick={() => setPendingCursor(undefined)}
                        >
                          Primeiras identificações
                        </button>
                        <button
                          className="secondary-button"
                          disabled={refreshing || !page.pending_next_cursor}
                          onClick={() =>
                            setPendingCursor(page.pending_next_cursor!)
                          }
                        >
                          Próximas identificações
                        </button>
                      </nav>
                    )}
                  </section>
                ))}
            </div>
          ))}
        </>
      )}
      <Link href="/conversions?view=batches" className="secondary-button">
        Ver conversões e lotes
      </Link>
    </section>
  );
}
