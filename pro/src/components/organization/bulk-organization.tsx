"use client";

import { useState, type FormEvent } from "react";
import type {
  BulkOrganizationResult,
  OrganizationCatalog,
  OrganizationService,
  OrganizedConversion,
} from "@/data/organization-service";
import styles from "./organization.module.css";

export function BulkOrganization({
  items,
  catalog,
  service,
  onSaved,
}: {
  items: OrganizedConversion[];
  catalog: OrganizationCatalog;
  service: OrganizationService;
  onSaved(result: BulkOrganizationResult): void;
}) {
  const [client, setClient] = useState("keep");
  const [account, setAccount] = useState("");
  const [period, setPeriod] = useState("");
  const [periodMode, setPeriodMode] = useState("add");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || (!period && client === "keep")) return;
    setBusy(true);
    setError(null);
    try {
      const result = await service.organizeBulk(
        items.map((item) => ({
          batch_id: item.batch_id,
          job_id: item.job_id,
          context: {
            ...item.context,
            client_id:
              client === "keep" ? item.context.client_id : client || null,
            account_id:
              client === "keep" ? item.context.account_id : account || null,
            period_ids: !period
              ? item.context.period_ids
              : periodMode === "remove"
                ? item.context.period_ids.filter((id) => id !== period)
                : [...new Set([...item.context.period_ids, period])],
          },
        })),
      );
      onSaved(result);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível organizar os arquivos.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className={styles.form}
      aria-label="Organizar selecionados"
      onSubmit={(event) => void submit(event)}
    >
      <h3>{items.length} arquivo(s) selecionado(s)</h3>
      <fieldset disabled={busy}>
        <label>
          Cliente para os selecionados
          <select
            value={client}
            onChange={(event) => {
              setClient(event.target.value);
              setAccount("");
            }}
          >
            <option value="keep">Manter cliente e conta de cada arquivo</option>
            <option value="">Remover cliente e conta</option>
            {catalog.clients.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {client !== "keep" && client && (
          <label>
            Conta para os selecionados
            <select
              value={account}
              onChange={(event) => setAccount(event.target.value)}
            >
              <option value="">Sem conta</option>
              {catalog.accounts
                .filter((item) => item.client_id === client)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
            </select>
          </label>
        )}
        <label>
          Período para os selecionados
          <select
            value={period}
            onChange={(event) => setPeriod(event.target.value)}
          >
            <option value="">Manter períodos de cada arquivo</option>
            {catalog.periods.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {period && (
          <label>
            Ação no período
            <select
              value={periodMode}
              onChange={(event) => setPeriodMode(event.target.value)}
            >
              <option value="add">Adicionar sem remover outros períodos</option>
              <option value="remove">Remover somente este período</option>
            </select>
          </label>
        )}
      </fieldset>
      <p>
        As alterações valem somente para os arquivos selecionados nesta página.
        Organizar não reprocessa nem consome cota.
      </p>
      {error && <p role="alert">{error}</p>}
      <button
        className="primary-button"
        disabled={busy || (!period && client === "keep")}
      >
        {busy ? "Salvando…" : "Aplicar organização"}
      </button>
    </form>
  );
}
