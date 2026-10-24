"use client";

import { useState, type FormEvent } from "react";
import type {
  OrganizationCatalog,
  OrganizationService,
} from "@/data/organization-service";
import styles from "./organization.module.css";

type Kind = "clients" | "accounts" | "periods";
const emptyValues = {
  name: "",
  tax_id: "",
  trade_name: "",
  client_id: "",
  bank_code: "",
  branch: "",
  account_number: "",
  start_date: "",
  end_date: "",
};

export function CatalogEditor({
  kind,
  catalog,
  service,
  onSaved,
}: {
  kind: Kind;
  catalog: OrganizationCatalog;
  service: OrganizationService;
  onSaved(): Promise<void>;
}) {
  const [values, setValues] = useState(emptyValues);
  const [editing, setEditing] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const field = (key: keyof typeof emptyValues, value: string) =>
    setValues((current) => ({ ...current, [key]: value }));
  const singular =
    kind === "clients" ? "cliente" : kind === "accounts" ? "conta" : "período";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      let saved: { id: string };
      if (kind === "clients")
        saved = await service.saveClient(
          {
            name: values.name,
            tax_id: values.tax_id || null,
            trade_name: values.trade_name,
          },
          editing,
        );
      else if (kind === "accounts")
        saved = await service.saveAccount(
          {
            name: values.name,
            client_id: values.client_id,
            bank_code: values.bank_code,
            branch: values.branch,
            account_number: values.account_number,
          },
          editing,
        );
      else
        saved = await service.savePeriod(
          {
            name: values.name,
            start_date: values.start_date,
            end_date: values.end_date,
          },
          editing,
        );
      setEditing(saved.id);
      await onSaved();
      setValues(emptyValues);
      setEditing(undefined);
      setMessage(
        kind === "clients"
          ? "Cliente salvo."
          : kind === "accounts"
            ? "Conta salva."
            : "Período salvo.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Não foi possível salvar.",
      );
    } finally {
      setBusy(false);
    }
  };
  function reset() {
    setEditing(undefined);
    setValues(emptyValues);
    setError(null);
    setMessage(null);
  }
  return (
    <div className={styles.catalogLayout}>
      <form
        className={styles.form}
        aria-label={`Cadastro de ${singular}`}
        onSubmit={(event) => void submit(event)}
      >
        <h2>{editing ? `Editar ${singular}` : `Cadastrar ${singular}`}</h2>
        <fieldset disabled={busy}>
          <label>
            Nome {kind === "accounts" ? "da conta" : `do ${singular}`}
            <input
              required
              maxLength={160}
              value={values.name}
              onChange={(event) => field("name", event.target.value)}
            />
          </label>
          {kind === "clients" && (
            <>
              <label>
                CPF/CNPJ (opcional)
                <input
                  maxLength={30}
                  value={values.tax_id}
                  onChange={(event) => field("tax_id", event.target.value)}
                />
              </label>
              <label>
                Nome fantasia (opcional)
                <input
                  maxLength={160}
                  value={values.trade_name}
                  onChange={(event) => field("trade_name", event.target.value)}
                />
              </label>
            </>
          )}
          {kind === "accounts" && (
            <>
              <label>
                Cliente da conta
                <select
                  required
                  value={values.client_id}
                  onChange={(event) => field("client_id", event.target.value)}
                >
                  <option value="">Selecione um cliente</option>
                  {catalog.clients.map((client) => (
                    <option key={client.id} value={client.id}>
                      {client.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Banco (código opcional)
                <input
                  maxLength={20}
                  value={values.bank_code}
                  onChange={(event) => field("bank_code", event.target.value)}
                />
              </label>
              <label>
                Agência (opcional)
                <input
                  maxLength={30}
                  value={values.branch}
                  onChange={(event) => field("branch", event.target.value)}
                />
              </label>
              <label>
                Número da conta (opcional)
                <input
                  maxLength={80}
                  value={values.account_number}
                  onChange={(event) =>
                    field("account_number", event.target.value)
                  }
                />
              </label>
              {!catalog.clients.length && (
                <p>
                  Cadastre um cliente para adicionar suas contas. Os arquivos
                  podem continuar sem cliente.
                </p>
              )}
            </>
          )}
          {kind === "periods" && (
            <>
              <label>
                Data inicial
                <input
                  required
                  type="date"
                  value={values.start_date}
                  onChange={(event) => field("start_date", event.target.value)}
                />
              </label>
              <label>
                Data final
                <input
                  required
                  type="date"
                  min={values.start_date || undefined}
                  value={values.end_date}
                  onChange={(event) => field("end_date", event.target.value)}
                />
              </label>
              <p>
                Use qualquer intervalo: uma semana, um mês ou datas que
                atravessem meses.
              </p>
            </>
          )}
        </fieldset>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
        {message && (
          <p role="status" className={styles.success}>
            {message}
          </p>
        )}
        <div className={styles.actions}>
          <button
            className="primary-button"
            disabled={busy || (kind === "accounts" && !catalog.clients.length)}
          >
            {busy ? "Salvando…" : "Salvar cadastro"}
          </button>
          {editing && (
            <button
              type="button"
              className="secondary-button"
              disabled={busy}
              onClick={reset}
            >
              Cancelar edição
            </button>
          )}
        </div>
      </form>
      <div
        className={styles.catalogItems}
        aria-label={`Lista de ${kind === "clients" ? "clientes" : kind === "accounts" ? "contas" : "períodos"}`}
      >
        {catalog[kind].length === 0 && (
          <p className={styles.empty}>Nenhum {singular} cadastrado.</p>
        )}
        {catalog[kind].map((item) => (
          <article className={styles.catalogItem} key={item.id}>
            <div>
              <strong>{item.name}</strong>
              {"tax_id" in item && (
                <small>
                  {item.trade_name}
                  {item.trade_name && item.tax_id ? " · " : ""}
                  {item.tax_id}
                </small>
              )}
              {"account_number" in item && (
                <small>
                  {
                    catalog.clients.find(
                      (client) => client.id === item.client_id,
                    )?.name
                  }{" "}
                  · {item.account_number || "Conta sem número informado"}
                </small>
              )}
              {"start_date" in item && (
                <small>
                  {formatDate(item.start_date)} a {formatDate(item.end_date)}
                </small>
              )}
            </div>
            <button
              className="secondary-button"
              disabled={busy}
              aria-label={`Editar ${item.name}`}
              onClick={() => {
                setEditing(item.id);
                setValues({
                  ...emptyValues,
                  ...item,
                  tax_id: "tax_id" in item ? item.tax_id || "" : "",
                });
                setError(null);
                setMessage(null);
              }}
            >
              Editar
            </button>
          </article>
        ))}
      </div>
    </div>
  );
}

export function formatDate(value: string): string {
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}
