"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import type {
  ConversionContext,
  OrganizationCatalog,
  OrganizationService,
  OrganizedConversion,
  OrganizationSuggestions,
} from "@/data/organization-service";
import { formatDate } from "./catalog-editor";
import { OrganizationSuggestionsPanel } from "./organization-suggestions";
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
  showOrganization = true,
  selected = false,
  onSelect,
  returnTo,
  onCatalogChanged = async () => {},
}: {
  item: OrganizedConversion;
  catalog: OrganizationCatalog;
  service: OrganizationService;
  onSaved(context: ConversionContext): void;
  available?: boolean;
  showOrganization?: boolean;
  selected?: boolean;
  onSelect?(checked: boolean): void;
  returnTo?: string;
  onCatalogChanged?(): Promise<void>;
}) {
  const [draft, setDraft] = useState(item.context);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [open, setOpen] = useState(false);
  const [periodEvidence, setPeriodEvidence] =
    useState<OrganizationSuggestions["period"]>(null);
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
          {showOrganization && onSelect && (
            <label className={styles.selection}>
              <input
                type="checkbox"
                aria-label={`Selecionar ${item.filename}`}
                checked={selected}
                onChange={(event) => onSelect(event.target.checked)}
              />{" "}
              Selecionar
            </label>
          )}
          <small>{statuses[item.status] || "Em processamento"}</small>
        </div>
        {item.review_url && (
          <Link
            className="secondary-button"
            href={
              returnTo
                ? `${item.review_url}?return_to=${encodeURIComponent(returnTo)}`
                : item.review_url
            }
          >
            Abrir revisão
          </Link>
        )}
      </div>
      {showOrganization && (
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
                : "Período de trabalho não definido"}
            </span>
          )}
        </div>
      )}
      {showOrganization && (
        <p className={styles.hint}>
          {periodEvidence?.document_coverage
            ? `Período do documento: ${formatDate(periodEvidence.start_date)} a ${formatDate(periodEvidence.end_date)}.`
            : "Período do documento a confirmar."}
        </p>
      )}
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
      {showOrganization && (
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
                {!item.is_expired && (
                  <OrganizationSuggestionsPanel
                    item={item}
                    catalog={catalog}
                    service={service}
                    onCatalogChanged={onCatalogChanged}
                    onPeriodEvidence={setPeriodEvidence}
                    onSelect={(changes) => {
                      setDraft((previous) => ({
                        ...previous,
                        ...changes,
                        period_ids: changes.period_ids
                          ? [
                              ...new Set([
                                ...previous.period_ids,
                                ...changes.period_ids,
                              ]),
                            ]
                          : previous.period_ids,
                      }));
                      setSaved(false);
                    }}
                  />
                )}
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
                      .filter(
                        (account) => account.client_id === draft.client_id,
                      )
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
                              : draft.period_ids.filter(
                                  (id) => id !== period.id,
                                ),
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
      )}
    </article>
  );
}
