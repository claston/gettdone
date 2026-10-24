"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import type {
  ConversionContext,
  OrganizationCatalog,
  OrganizationService,
  OrganizedConversion,
} from "@/data/organization-service";
import { formatDate } from "./catalog-editor";
import styles from "./organization.module.css";

const statuses: Record<string, string> = {
  uploading: "Aguardando envio",
  uploaded: "Enviado",
  queued: "Na fila",
  running: "Processando",
  retrying: "Tentando novamente",
  completed: "Concluído",
  failed: "Falhou",
  expired: "Expirado",
};

export function ConversionOrganization({
  item,
  catalog,
  service,
  onSaved,
  available = true,
}: {
  item: OrganizedConversion;
  catalog: OrganizationCatalog;
  service: OrganizationService;
  onSaved(context: ConversionContext): void;
  available?: boolean;
}) {
  const [draft, setDraft] = useState(item.context);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [open, setOpen] = useState(false);
  const client = catalog.clients.find(
    (client) => client.id === item.context.client_id,
  );
  const account = catalog.accounts.find(
    (account) => account.id === item.context.account_id,
  );
  const periods = catalog.periods.filter((period) =>
    item.context.period_ids.includes(period.id),
  );
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !available) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const context = await service.organize(item.batch_id, item.job_id, draft);
      setDraft(context);
      setSaved(true);
      onSaved(context);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível salvar a organização.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className={styles.conversion} aria-label={item.filename}>
      <div className={styles.conversionHeader}>
        <div>
          <h3>{item.filename}</h3>
          <small>{statuses[item.status] || "Em processamento"}</small>
        </div>
        {item.review_url && (
          <Link className="secondary-button" href={item.review_url}>
            Abrir revisão
          </Link>
        )}
      </div>
      <div className={styles.chips}>
        <span>
          {client?.name ||
            (item.context.client_id ? "Cliente a carregar" : "Sem cliente")}
        </span>
        {account && <span>{account.name}</span>}
        {periods.length ? (
          periods.map((period) => <span key={period.id}>{period.name}</span>)
        ) : (
          <span>
            {item.context.period_ids.length
              ? "Períodos a carregar"
              : "Período a confirmar"}
          </span>
        )}
      </div>
      {item.movement_start_date && item.movement_end_date && (
        <p className={styles.hint}>
          Datas dos movimentos identificados:{" "}
          {formatDate(item.movement_start_date)} a{" "}
          {formatDate(item.movement_end_date)}.
        </p>
      )}
      {item.is_expired && (
        <p className={styles.hint}>
          O arquivo expirou. A organização não renova sua disponibilidade.
        </p>
      )}
      <div className={styles.details}>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          Organizar
        </button>
        {open && (
          <form
            className={styles.form}
            aria-label={`Organizar ${item.filename}`}
            onSubmit={(event) => void submit(event)}
          >
            {!available && (
              <p>Carregue os cadastros para alterar a organização.</p>
            )}
            <fieldset disabled={busy || !available}>
              <label>
                Cliente do arquivo
                <select
                  value={draft.client_id || ""}
                  onChange={(event) => {
                    setDraft({
                      ...draft,
                      client_id: event.target.value || null,
                      account_id: null,
                    });
                    setSaved(false);
                  }}
                >
                  <option value="">Sem cliente</option>
                  {catalog.clients.map((client) => (
                    <option key={client.id} value={client.id}>
                      {client.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Conta do arquivo
                <select
                  value={draft.account_id || ""}
                  disabled={!draft.client_id}
                  onChange={(event) => {
                    setDraft({
                      ...draft,
                      account_id: event.target.value || null,
                    });
                    setSaved(false);
                  }}
                >
                  <option value="">Sem conta</option>
                  {catalog.accounts
                    .filter((account) => account.client_id === draft.client_id)
                    .map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.name}
                      </option>
                    ))}
                </select>
              </label>
              <fieldset className={styles.periodPicker}>
                <legend>Períodos de trabalho</legend>
                {!catalog.periods.length && (
                  <p>
                    Cadastre um período para organizar este arquivo por datas.
                  </p>
                )}
                {catalog.periods.map((period) => (
                  <label key={period.id} className={styles.checkbox}>
                    <input
                      type="checkbox"
                      checked={draft.period_ids.includes(period.id)}
                      onChange={(event) => {
                        setDraft({
                          ...draft,
                          period_ids: event.target.checked
                            ? [...draft.period_ids, period.id]
                            : draft.period_ids.filter((id) => id !== period.id),
                        });
                        setSaved(false);
                      }}
                    />
                    {period.name} ({formatDate(period.start_date)} a{" "}
                    {formatDate(period.end_date)})
                  </label>
                ))}
              </fieldset>
            </fieldset>
            <p className={styles.hint}>
              Organizar não reprocessa o arquivo nem consome nova cota. Os
              períodos são vínculos de trabalho; o arquivo original permanece
              completo.
            </p>
            {error && (
              <p role="alert" className={styles.error}>
                {error}
              </p>
            )}
            {saved && (
              <p role="status" className={styles.success}>
                Organização salva.
              </p>
            )}
            <button className="primary-button" disabled={busy || !available}>
              {busy ? "Salvando…" : "Salvar organização"}
            </button>
          </form>
        )}
      </div>
    </article>
  );
}
