"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  organizationService,
  type OrganizationCatalog,
  type OrganizationService,
  type OrganizationClient,
} from "@/data/organization-service";
import { receivedService } from "@/data/received-statements-service";
import styles from "./documents/documents.module.css";

export function ClientsPage({
  service = organizationService,
  financialCenterVisible = false,
}: {
  service?: OrganizationService;
  financialCenterVisible?: boolean;
}) {
  const [catalog, setCatalog] = useState<OrganizationCatalog | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<OrganizationClient | "new" | null>(
    null,
  );
  useEffect(() => {
    const abort = new AbortController();
    void service
      .catalog(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) {
          setCatalog(value);
          setError("");
        }
      })
      .catch(() => {
        if (!abort.signal.aborted) {
          setCatalog(null);
          setError("Não foi possível carregar os clientes. Tente novamente.");
        }
      });
    return () => abort.abort();
  }, [service, attempt]);
  const normalize = (value: string) =>
    value
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("pt-BR");
  const term = normalize(query.trim());
  const customers =
    catalog?.clients
      .filter(
        (c) =>
          normalize(`${c.name} ${c.trade_name} ${c.tax_id ?? ""}`).includes(
            term,
          ) ||
          Boolean(
            term.replace(/\D/g, "") &&
            /^[\d./\-\s]+$/.test(term) &&
            c.tax_id?.includes(term.replace(/\D/g, "")),
          ),
      )
      .sort((a, b) => a.name.localeCompare(b.name, "pt-BR")) ?? [];
  return (
    <main className={`standard-page ${styles.page}`}>
      <header className="page-title">
        <div>
          <h1>Clientes</h1>
          <p>
            Sua carteira de clientes, documentos e movimentações financeiras.
          </p>
        </div>
        <button className="primary-button" onClick={() => setEditing("new")}>
          Novo cliente
        </button>
      </header>
      {editing && (
        <ClientForm
          key={typeof editing === "string" ? editing : editing.id}
          client={editing === "new" ? undefined : editing}
          service={service}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            setAttempt((a) => a + 1);
          }}
        />
      )}
      {error ? (
        <div role="alert">
          <p>{error}</p>
          <button
            className="secondary-button"
            onClick={() => setAttempt((a) => a + 1)}
          >
            Tentar novamente
          </button>
        </div>
      ) : !catalog ? (
        <p role="status">Carregando clientes…</p>
      ) : (
        <>
          <label className={styles.search}>
            Buscar por nome, CPF ou CNPJ
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              maxLength={160}
            />
          </label>
          <div className={styles.list}>
            {customers.map((client) => (
              <article className={styles.client} key={client.id}>
                <div>
                  <Link
                    className={styles.clientName}
                    href={`/documents?client=${encodeURIComponent(client.id)}`}
                  >
                    {client.name}
                  </Link>
                  <p>{client.tax_id || "CPF/CNPJ não informado"}</p>
                </div>
                <div className={styles.actions}>
                  <Link
                    href={`/documents?client=${encodeURIComponent(client.id)}`}
                  >
                    Documentos
                  </Link>
                  <Link
                    href={`/documents?client=${encodeURIComponent(client.id)}&type=statements`}
                  >
                    Extratos
                  </Link>
                  {financialCenterVisible && (
                    <Link href={`/financial/${encodeURIComponent(client.id)}`}>
                      Central Financeira
                    </Link>
                  )}
                  <button
                    className="secondary-button"
                    onClick={() => setEditing(client)}
                    aria-label={`Editar ${client.name}`}
                  >
                    Editar
                  </button>
                </div>
              </article>
            ))}
          </div>
          {!customers.length && (
            <p>
              {query
                ? "Nenhum cliente encontrado."
                : "Nenhum cliente cadastrado."}
            </p>
          )}
        </>
      )}
    </main>
  );
}

function ClientForm({
  client,
  service,
  onSaved,
  onCancel,
}: {
  client?: OrganizationClient;
  service: OrganizationService;
  onSaved(): void;
  onCancel(): void;
}) {
  const [name, setName] = useState(client?.name ?? "");
  const [tax, setTax] = useState(client?.tax_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <form
      className={styles.panel}
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return;
        setBusy(true);
        setError("");
        try {
          if (client?.updated_at)
            await receivedService.edit(client.id, {
              name: name.trim(),
              tax_id: tax.trim() || null,
              expected_updated_at: client.updated_at,
            });
          else if (client)
            throw new Error(
              "Atualize a lista de clientes antes de editar este cadastro.",
            );
          else
            await service.saveClient({
              name: name.trim(),
              tax_id: tax.trim() || null,
              trade_name: "",
            });
          onSaved();
        } catch (caught) {
          setError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível salvar o cliente.",
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>{client ? "Editar cliente" : "Novo cliente"}</h2>
      <fieldset disabled={busy} className={styles.fields}>
        <label>
          Nome do cliente
          <input
            required
            maxLength={160}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          CPF/CNPJ (opcional)
          <input
            maxLength={30}
            value={tax}
            onChange={(e) => setTax(e.target.value)}
          />
        </label>
      </fieldset>
      {error && <p role="alert">{error}</p>}
      <div className={styles.actions}>
        <button className="primary-button" disabled={busy}>
          {busy ? "Salvando…" : "Salvar cliente"}
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
