"use client";

import { useState } from "react";
import type {
  ConversionContext,
  OrganizationCatalog,
  OrganizationService,
  OrganizationSuggestions,
  OrganizedConversion,
} from "@/data/organization-service";
import styles from "./organization.module.css";

export function OrganizationSuggestionsPanel({
  item,
  catalog,
  service,
  onSelect,
  onCatalogChanged,
  onPeriodEvidence,
}: {
  item: OrganizedConversion;
  catalog: OrganizationCatalog;
  service: OrganizationService;
  onSelect(changes: Partial<ConversionContext>): void;
  onCatalogChanged(): Promise<void>;
  onPeriodEvidence(period: OrganizationSuggestions["period"]): void;
}) {
  const [suggestions, setSuggestions] =
    useState<OrganizationSuggestions | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [period, setPeriod] = useState({
    name: "",
    start_date: "",
    end_date: "",
  });
  const [createdPeriod, setCreatedPeriod] = useState<string | null>(null);
  const [periodSelected, setPeriodSelected] = useState(false);
  const load = async () => {
    setBusy(true);
    setError(null);
    try {
      const value = await service.suggestions(item.batch_id, item.job_id);
      setSuggestions(value);
      onPeriodEvidence(value.period);
      if (value.period)
        setPeriod({
          name: "Período de trabalho",
          start_date: value.period.start_date,
          end_date: value.period.end_date,
        });
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível buscar sugestões.",
      );
    } finally {
      setBusy(false);
    }
  };
  const selectPeriod = async () => {
    if (
      busy ||
      !period.name.trim() ||
      !period.start_date ||
      !period.end_date ||
      period.start_date > period.end_date
    )
      return;
    setBusy(true);
    setError(null);
    try {
      const existing = catalog.periods.find(
        (item) =>
          item.name === period.name &&
          item.start_date === period.start_date &&
          item.end_date === period.end_date,
      );
      const id =
        createdPeriod ?? existing?.id ?? (await service.savePeriod(period)).id;
      setCreatedPeriod(id);
      await onCatalogChanged();
      onSelect({ period_ids: [id] });
      setPeriodSelected(true);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível criar o período.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className={styles.suggestions}
      aria-label="Sugestões de organização"
    >
      {!suggestions && (
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={() => void load()}
        >
          {busy ? "Buscando…" : "Buscar sugestões"}
        </button>
      )}
      {suggestions && (
        <>
          <p>
            Confira a evidência. Usar uma sugestão preenche o formulário; o
            vínculo só é confirmado ao salvar.
          </p>
          {item.context.client_id && (
            <p>
              Este arquivo já tem cliente. Escolher outro cliente substituirá o
              vínculo ao salvar.
            </p>
          )}
          {suggestions.clients.map((suggestion) => {
            const client = catalog.clients.find(
              (item) => item.id === suggestion.id,
            );
            return (
              client && (
                <div key={suggestion.id}>
                  <p>{suggestion.evidence}</p>
                  {suggestion.confidence === "ambiguous" && (
                    <p>
                      Há mais de uma correspondência. Confira qual cliente é o
                      titular.
                    </p>
                  )}
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      onSelect({ client_id: client.id, account_id: null })
                    }
                  >
                    Usar cliente {client.name}
                  </button>
                </div>
              )
            );
          })}
          {suggestions.accounts.map((suggestion) => {
            const account = catalog.accounts.find(
              (item) => item.id === suggestion.id,
            );
            return (
              account && (
                <div key={suggestion.id}>
                  <p>{suggestion.evidence}</p>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() =>
                      onSelect({
                        client_id: account.client_id,
                        account_id: account.id,
                      })
                    }
                  >
                    Usar conta {account.name}
                  </button>
                </div>
              )
            );
          })}
          {!suggestions.clients.length && !suggestions.accounts.length && (
            <p>
              Não há evidência suficiente para sugerir um cliente ou uma conta
              cadastrada. Você pode organizar manualmente.
            </p>
          )}
          {suggestions.period ? (
            <>
              <p>{suggestions.period.evidence}</p>
              <p>
                {suggestions.period.document_coverage
                  ? "Intervalo documental identificado"
                  : "Recorte sugerido para o trabalho"}
                : {suggestions.period.start_date} a{" "}
                {suggestions.period.end_date}.
              </p>
              {!periodSelected ? (
                <fieldset disabled={busy || createdPeriod !== null}>
                  <label>
                    Nome do período sugerido
                    <input
                      value={period.name}
                      onChange={(event) =>
                        setPeriod({ ...period, name: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    Início do trabalho
                    <input
                      type="date"
                      value={period.start_date}
                      onChange={(event) =>
                        setPeriod({ ...period, start_date: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    Fim do trabalho
                    <input
                      type="date"
                      value={period.end_date}
                      min={period.start_date}
                      onChange={(event) =>
                        setPeriod({ ...period, end_date: event.target.value })
                      }
                    />
                  </label>
                </fieldset>
              ) : (
                <p>
                  Período selecionado. Salve a organização para confirmar o
                  vínculo.
                </p>
              )}
              {!periodSelected && (
                <button
                  type="button"
                  className="secondary-button"
                  disabled={
                    busy ||
                    !period.name.trim() ||
                    !period.start_date ||
                    !period.end_date ||
                    period.start_date > period.end_date
                  }
                  onClick={() => void selectPeriod()}
                >
                  {busy ? "Preparando…" : "Criar período e selecionar"}
                </button>
              )}
            </>
          ) : (
            <p>Período do documento a confirmar. Nenhuma data foi presumida.</p>
          )}
        </>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </section>
  );
}
