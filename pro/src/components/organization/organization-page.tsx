"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  organizationService,
  type ConversionContext,
  type OrganizationCatalog,
  type OrganizationFilters,
  type OrganizationPageResult,
  type OrganizationService,
  type OrganizedConversion,
} from "@/data/organization-service";
import { CatalogEditor, formatDate } from "./catalog-editor";
import { ConversionOrganization } from "./conversion-organization";
import styles from "./organization.module.css";

const emptyCatalog: OrganizationCatalog = {
  clients: [],
  accounts: [],
  periods: [],
};
const emptyFilters: OrganizationFilters = {
  client: "",
  account: "",
  period: "",
};
type CatalogTab = "clients" | "accounts" | "periods";

export function OrganizationPage({
  mode,
  service,
}: {
  mode: "clients" | "periods" | "conversions";
  service?: OrganizationService;
}) {
  const active = service ?? organizationService;
  const [catalog, setCatalog] = useState<OrganizationCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [tab, setTab] = useState<CatalogTab>(
    mode === "periods" ? "periods" : "clients",
  );
  const [filters, setFilters] = useState(emptyFilters);
  const [cursors, setCursors] = useState<Array<string | undefined>>([
    undefined,
  ]);
  const cursor = cursors.at(-1);
  const [attempt, setAttempt] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const request = useMemo(
    () => ({ active, filters, cursor, attempt }),
    [active, filters, cursor, attempt],
  );
  const [result, setResult] = useState<{
    request: typeof request;
    page: OrganizationPageResult | null;
    error: string | null;
  } | null>(null);
  const current = result?.request === request ? result : null;
  const loading = !current;

  useEffect(() => {
    const abort = new AbortController();
    void active
      .catalog(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) {
          setCatalog(value);
          setCatalogError(null);
        }
      })
      .catch((caught: unknown) => {
        if (!abort.signal.aborted)
          setCatalogError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível carregar clientes e períodos.",
          );
      });
    return () => abort.abort();
  }, [active, catalogAttempt]);

  useEffect(() => {
    const abort = new AbortController();
    void request.active
      .listConversions(request.filters, request.cursor, abort.signal)
      .then((page) => {
        if (!abort.signal.aborted) setResult({ request, page, error: null });
      })
      .catch((caught: unknown) => {
        if (!abort.signal.aborted)
          setResult({
            request,
            page: null,
            error:
              caught instanceof Error
                ? caught.message
                : "Não foi possível carregar as conversões.",
          });
      });
    return () => abort.abort();
  }, [request]);

  const refreshCatalog = async () => {
    const value = await active.catalog();
    setCatalog(value);
    setCatalogError(null);
  };
  const filter = (changes: Partial<OrganizationFilters>) => {
    setFilters((previous) => ({ ...previous, ...changes }));
    setCursors([undefined]);
    setNotice(null);
  };
  const matches = (item: OrganizedConversion) =>
    (!filters.client ||
      (filters.client === "unassigned"
        ? !item.context.client_id
        : item.context.client_id === filters.client)) &&
    (!filters.period || item.context.period_ids.includes(filters.period)) &&
    (!filters.account || item.context.account_id === filters.account);
  const onSaved = (item: OrganizedConversion, context: ConversionContext) => {
    const updated = { ...item, context };
    setNotice(
      matches(updated)
        ? null
        : "Organização salva. O arquivo pode ser encontrado nos novos filtros.",
    );
    setResult((previous) =>
      previous?.request === request && previous.page
        ? {
            ...previous,
            page: {
              ...previous.page,
              items: previous.page.items
                .map((row) => (row.job_id === item.job_id ? updated : row))
                .filter(matches),
            },
          }
        : previous,
    );
  };
  const values = catalog ?? emptyCatalog;
  return (
    <section
      className={`standard-page ${styles.page}`}
      aria-label="Organização de conversões"
    >
      {mode !== "conversions" && (
        <>
          <div className="page-title">
            <div>
              <span className="eyebrow">Organização do trabalho</span>
              <h1>
                {mode === "periods"
                  ? "Períodos de trabalho"
                  : "Clientes e contas"}
              </h1>
              <p>
                Organize seus arquivos por cliente, conta e datas. Você também
                pode trabalhar sem cliente.
              </p>
            </div>
            <Link className="primary-button" href="/conversions">
              Nova conversão
            </Link>
          </div>
          <nav className={styles.tabs} aria-label="Cadastros">
            {(
              [
                ["clients", "Clientes"],
                ["accounts", "Contas"],
                ["periods", "Períodos"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={tab === key ? "primary-button" : "secondary-button"}
                aria-pressed={tab === key}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </nav>
          {catalogError ? (
            <div className={styles.error} role="alert">
              <p>{catalogError}</p>
              <button
                className="secondary-button"
                onClick={() => setCatalogAttempt((value) => value + 1)}
              >
                Tentar carregar cadastros novamente
              </button>
            </div>
          ) : catalog ? (
            <CatalogEditor
              key={tab}
              kind={tab}
              catalog={catalog}
              service={active}
              onSaved={refreshCatalog}
            />
          ) : (
            <p role="status">Carregando cadastros…</p>
          )}
        </>
      )}
      <div className={styles.browserHeader}>
        <div>
          <h2>Todas as conversões</h2>
          <p>
            Os filtros de cliente e período podem ser usados separadamente ou em
            conjunto.
          </p>
        </div>
        <button
          className="secondary-button"
          onClick={() => {
            setAttempt((value) => value + 1);
            setCatalogAttempt((value) => value + 1);
            setNotice(null);
          }}
        >
          Atualizar conversões
        </button>
      </div>
      {mode === "conversions" && (
        <p className={styles.shortcuts}>
          <Link href="/clients">Cadastrar clientes e contas</Link>
          <Link href="/periods">Cadastrar períodos</Link>
        </p>
      )}
      {mode === "conversions" && catalogError && (
        <p role="alert" className={styles.error}>
          {catalogError}
        </p>
      )}
      <div className={styles.filters}>
        <label>
          Filtrar por cliente
          <select
            value={filters.client}
            onChange={(event) =>
              filter({ client: event.target.value, account: "" })
            }
          >
            <option value="">Todos os clientes</option>
            <option value="unassigned">Sem cliente</option>
            {values.clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Filtrar por conta
          <select
            value={filters.account}
            onChange={(event) => filter({ account: event.target.value })}
          >
            <option value="">Todas as contas</option>
            {values.accounts
              .filter(
                (account) =>
                  !filters.client || account.client_id === filters.client,
              )
              .map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
          </select>
        </label>
        <label>
          Filtrar por período
          <select
            value={filters.period}
            onChange={(event) => filter({ period: event.target.value })}
          >
            <option value="">Todos os períodos</option>
            {values.periods.map((period) => (
              <option key={period.id} value={period.id}>
                {period.name} ({formatDate(period.start_date)} a{" "}
                {formatDate(period.end_date)})
              </option>
            ))}
          </select>
        </label>
        <button
          className="secondary-button"
          onClick={() => {
            setFilters(emptyFilters);
            setCursors([undefined]);
            setNotice(null);
          }}
        >
          Limpar filtros
        </button>
      </div>
      {notice && (
        <p role="status" className={styles.success}>
          {notice}
        </p>
      )}
      {loading ? (
        <p role="status" className={styles.empty}>
          Carregando conversões…
        </p>
      ) : current?.error ? (
        <p role="alert" className={styles.error}>
          {current.error}
        </p>
      ) : (
        current?.page && (
          <>
            {!current.page.items.length && (
              <p className={styles.empty}>
                {current.page.next_cursor
                  ? "Nenhum arquivo com esses filtros nesta página. Consulte a próxima página para continuar."
                  : "Nenhuma conversão encontrada com os filtros selecionados."}
              </p>
            )}
            <div className={styles.conversions}>
              {current.page.items.map((item) => (
                <ConversionOrganization
                  key={`${item.job_id}:${attempt}`}
                  item={item}
                  catalog={values}
                  service={active}
                  available={catalog !== null && !catalogError}
                  onSaved={(context) => onSaved(item, context)}
                />
              ))}
            </div>
          </>
        )
      )}
      <div className={styles.pagination}>
        <button
          className="secondary-button"
          disabled={loading || cursors.length === 1}
          onClick={() => setCursors((value) => value.slice(0, -1))}
        >
          Página anterior
        </button>
        <span>Página {cursors.length}</span>
        <button
          className="secondary-button"
          disabled={loading || !current?.page?.next_cursor}
          onClick={() => {
            const next = current?.page?.next_cursor;
            if (next) setCursors((value) => [...value, next]);
          }}
        >
          Próxima página
        </button>
      </div>
    </section>
  );
}
