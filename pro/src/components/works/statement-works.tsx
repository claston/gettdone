"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  organizationService,
  type OrganizationCatalog,
  type OrganizationService,
} from "@/data/organization-service";
import {
  statementWorkService,
  type StatementWork,
  type StatementWorkService,
  type WorkSummary,
} from "@/data/statement-work-service";
import { WorkDetail } from "./work-detail";
import styles from "../organization/organization.module.css";

export function StatementWorks({
  initialId,
  initialClient = "",
  initialPeriod = "",
  service = statementWorkService,
  organization = organizationService,
}: {
  initialId?: string;
  initialClient?: string;
  initialPeriod?: string;
  service?: StatementWorkService;
  organization?: OrganizationService;
}) {
  const [catalog, setCatalog] = useState<OrganizationCatalog | null>(null);
  const [work, setWork] = useState<StatementWork | null>(null);
  const [items, setItems] = useState<WorkSummary[]>([]);
  const [client, setClient] = useState(initialClient);
  const [period, setPeriod] = useState(initialPeriod);
  const [filters, setFilters] = useState({
    client: initialClient,
    period: initialPeriod,
    state: "",
  });
  const [offset, setOffset] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [newPeriod, setNewPeriod] = useState({
    name: "",
    start_date: "",
    end_date: "",
  });
  useEffect(() => {
    let active = true;
    void organization
      .catalog()
      .then((value) => {
        if (active) {
          setCatalog(value);
          if (value.clients.length === 1 && !initialClient)
            setClient(value.clients[0].id);
        }
      })
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível carregar os cadastros.",
          );
      });
    return () => {
      active = false;
    };
  }, [organization, initialClient, attempt]);
  useEffect(() => {
    let active = true;
    const action = initialId
      ? service.get(initialId).then((value) => {
          if (active) setWork(value);
        })
      : service.list(filters, offset).then((value) => {
          if (active) {
            setItems(value.items);
            setNext(value.next_offset);
          }
        });
    void action
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível carregar trabalhos.",
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [service, initialId, filters, offset, attempt]);
  const run = async (action: () => Promise<StatementWork>) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const value = await action();
      setWork(value);
      window.history.replaceState(
        null,
        "",
        `/works/${encodeURIComponent(value.id)}`,
      );
      setMessage("Alteração salva.");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível salvar o trabalho.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className={`standard-page ${styles.page}`}>
      <div className={styles.actions}>
        <Link href="/conversions" className="secondary-button">
          Ir para Conversões
        </Link>
        <button
          type="button"
          disabled={busy}
          className="secondary-button"
          onClick={() => {
            setError(null);
            if (work) void run(() => service.get(work.id));
            else {
              setLoading(true);
              setAttempt(attempt + 1);
            }
          }}
        >
          Atualizar trabalhos
        </button>
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error} Confira as alterações e atualize antes de tentar novamente.
        </p>
      )}
      {message && (
        <p role="status" className={styles.success}>
          {message}
        </p>
      )}
      {loading && <p role="status">Carregando trabalhos…</p>}
      <fieldset className="work-content" disabled={busy}>
        {work ? (
          <WorkDetail
            key={work.id}
            work={work}
            service={service}
            organization={organization}
            run={run}
          />
        ) : (
          <>
            <h1>Trabalhos</h1>
            <p>
              Prepare extratos por cliente e período. Criar um trabalho não
              exige novo upload.
            </p>
            {catalog && (
              <form
                className={styles.form}
                aria-label="Criar trabalho"
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(() => service.create(client, period));
                }}
              >
                <label>
                  Cliente do trabalho
                  <select
                    required
                    value={client}
                    onChange={(event) => setClient(event.target.value)}
                  >
                    <option value="">Selecione um cliente</option>
                    {catalog.clients.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Período do trabalho
                  <select
                    required
                    value={period}
                    onChange={(event) => setPeriod(event.target.value)}
                  >
                    <option value="">Selecione um período</option>
                    {catalog.periods.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name} ({item.start_date} a {item.end_date})
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="primary-button"
                  disabled={!client || !period}
                >
                  Criar ou abrir trabalho
                </button>
                <p>
                  <Link href="/clients">Cadastrar cliente ou conta</Link> ·{" "}
                  <Link href="/periods">Gerenciar períodos</Link>
                </p>
              </form>
            )}
            <details>
              <summary>Criar período com datas livres</summary>
              <form
                className={styles.form}
                onSubmit={async (event) => {
                  event.preventDefault();
                  setBusy(true);
                  setError(null);
                  try {
                    const value = await organization.savePeriod(newPeriod);
                    setCatalog(await organization.catalog());
                    setPeriod(value.id);
                  } catch (caught) {
                    setError(
                      caught instanceof Error
                        ? caught.message
                        : "Não foi possível criar o período.",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <label>
                  Nome do novo período
                  <input
                    required
                    maxLength={160}
                    value={newPeriod.name}
                    onChange={(event) =>
                      setNewPeriod({ ...newPeriod, name: event.target.value })
                    }
                  />
                </label>
                <label>
                  Data inicial do novo período
                  <input
                    required
                    type="date"
                    value={newPeriod.start_date}
                    onChange={(event) =>
                      setNewPeriod({
                        ...newPeriod,
                        start_date: event.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  Data final do novo período
                  <input
                    required
                    type="date"
                    min={newPeriod.start_date}
                    value={newPeriod.end_date}
                    onChange={(event) =>
                      setNewPeriod({
                        ...newPeriod,
                        end_date: event.target.value,
                      })
                    }
                  />
                </label>
                <button className="secondary-button">
                  Criar e selecionar período
                </button>
              </form>
            </details>
            <div className={styles.filters}>
              {(
                [
                  [
                    "client",
                    "Filtrar trabalhos por cliente",
                    catalog?.clients ?? [],
                  ],
                  [
                    "period",
                    "Filtrar trabalhos por período",
                    catalog?.periods ?? [],
                  ],
                  [
                    "state",
                    "Filtrar trabalhos por situação",
                    [
                      { id: "open", name: "Em preparação" },
                      { id: "completed", name: "Concluído" },
                    ],
                  ],
                ] as const
              ).map(([key, label, values]) => (
                <label key={key}>
                  {label}
                  <select
                    value={filters[key]}
                    onChange={(event) => {
                      setFilters({ ...filters, [key]: event.target.value });
                      setOffset(0);
                      setLoading(true);
                    }}
                  >
                    <option value="">Todos</option>
                    {values.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            {!loading && !items.length && <p>Nenhum trabalho encontrado.</p>}
            {items.map((item) => (
              <article className={styles.catalogItem} key={item.id}>
                <div>
                  <h2>
                    {item.client.name} · {item.period.name}
                  </h2>
                  <p>
                    {item.state === "completed"
                      ? item.conclusion === "with_reservations"
                        ? "Concluído com ressalvas"
                        : "Concluído"
                      : "Em preparação"}{" "}
                    · {item.accounts_count} conta(s) · {item.documents_count}{" "}
                    extrato(s) · {item.blockers_count} bloqueio(s) ·{" "}
                    {item.reservations_count} ressalva(s)
                  </p>
                  {item.needs_reassessment && <p>Reavaliação necessária</p>}
                </div>
                <Link
                  className="secondary-button"
                  href={`/works/${encodeURIComponent(item.id)}`}
                >
                  Abrir trabalho
                </Link>
              </article>
            ))}
            <div className={styles.pagination}>
              <button
                type="button"
                disabled={!offset}
                onClick={() => {
                  setOffset(Math.max(0, offset - 20));
                  setLoading(true);
                }}
              >
                Página anterior
              </button>
              <button
                type="button"
                disabled={next === null}
                onClick={() => {
                  if (next !== null) {
                    setOffset(next);
                    setLoading(true);
                  }
                }}
              >
                Próxima página
              </button>
            </div>
          </>
        )}
      </fieldset>
    </main>
  );
}
