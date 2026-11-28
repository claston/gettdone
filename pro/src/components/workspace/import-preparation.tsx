"use client";

import { useCallback, useEffect, useState } from "react";
import {
  officeService,
  type ImportPreparation,
  type RetentionPolicy,
} from "@/data/office-service";
import {
  organizationService,
  type ConversionContext,
} from "@/data/organization-service";
import { conferenceService } from "@/data/document-conference-service";
import styles from "./import-preparation.module.css";

export function useImportPreparation(
  enabled: boolean,
  target: string,
  suggestedClient: string,
) {
  const [data, setData] = useState<ImportPreparation | null>(null);
  const [draft, setDraft] = useState<ConversionContext | null>(null);
  const [policy, setPolicy] = useState<RetentionPolicy | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const jobId = target.split(":")[0];
  useEffect(() => {
    if (!enabled || !jobId) return;
    const abort = new AbortController();
    void (async () => {
      try {
        const retention = await officeService.retention(abort.signal);
        const result = await officeService.prepare(
          jobId,
          retention.read_only,
          abort.signal,
        );
        if (abort.signal.aborted) return;
        setPolicy(retention);
        setData(result);
        setDraft({
          ...result.context,
          client_id:
            result.context.client_id ||
            (result.catalog.clients.some((c) => c.id === suggestedClient)
              ? suggestedClient
              : null),
        });
        setError("");
      } catch (caught) {
        if (!abort.signal.aborted) {
          setData(null);
          setError(
            caught instanceof Error
              ? caught.message
              : "Não foi possível preparar a revisão.",
          );
        }
      }
    })();
    return () => abort.abort();
  }, [enabled, jobId, target, suggestedClient, attempt]);

  const confirmContext = useCallback(
    async (id: string) => {
      if (
        !data ||
        data.job_id !== id ||
        !draft?.client_id ||
        !policy ||
        policy.read_only
      )
        throw new Error(
          "Confira o cliente e aguarde a preparação da revisão antes de confirmar.",
        );
      if (
        draft.client_id === data.context.client_id &&
        draft.account_id === data.context.account_id
      )
        return data.context.version;
      const context = await organizationService.organize(
        data.batch_id,
        id,
        draft,
      );
      setData({ ...data, context, matching: null });
      setDraft(context);
      const prepared = await officeService.prepare(id);
      setData(prepared);
      return context.version;
    },
    [data, draft, policy],
  );

  const link = async (
    match: NonNullable<ImportPreparation["matching"]>["matches"][number],
  ) => {
    if (
      !data?.context.client_id ||
      !data.matching?.source ||
      policy?.read_only ||
      busy
    )
      return;
    setBusy(true);
    setError("");
    const signal = new AbortController().signal;
    try {
      if (match.linked && match.link_id && match.link_version)
        await conferenceService.undo(
          data.context.client_id,
          match.id,
          match.link_id,
          {
            expected_version: match.link_version,
            operation_id: crypto.randomUUID(),
          },
          signal,
        );
      else
        await conferenceService.confirm(
          data.context.client_id,
          match.id,
          {
            job_id: data.job_id,
            source: data.matching.source,
            movement_version: match.version,
            operation_id: crypto.randomUUID(),
          },
          signal,
        );
      setAttempt((value) => value + 1);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível salvar o vínculo.",
      );
    } finally {
      setBusy(false);
    }
  };
  return {
    data: data?.job_id === jobId ? data : null,
    draft,
    setDraft,
    policy,
    error,
    busy,
    confirmContext,
    link,
    retry: () => setAttempt((value) => value + 1),
  };
}

export function ImportPreparationPanel({
  preparation,
  disabled,
}: {
  preparation: ReturnType<typeof useImportPreparation>;
  disabled: boolean;
}) {
  const { data, draft, policy, error, busy } = preparation;
  return (
    <section className={styles.panel} aria-label="Revisão da importação">
      <div className={styles.heading}>
        <div>
          <strong>2. Revisar importação</strong>
          <p>Confira o original e os dados. Confirme para seguir.</p>
        </div>
        {policy &&
          (policy.retained_while_paid || policy.delete_after) &&
          (policy.read_only ? (
            <p className={styles.retention}>
              Consulta e download até{" "}
              {new Date(policy.delete_after!).toLocaleDateString("pt-BR")}.
              Renove para voltar a revisar.
            </p>
          ) : (
            <details className={styles.retention}>
              <summary>Prazo de guarda</summary>
              <p>
                Documentos guardados enquanto seu plano estiver pago e por 30
                dias após o término.
              </p>
            </details>
          ))}
      </div>
      {error && (
        <p role="alert">
          {error}{" "}
          <button className="secondary-button" onClick={preparation.retry}>
            Tentar novamente
          </button>
        </p>
      )}
      {!data && !error && (
        <p role="status">Identificando cliente e procurando vínculos…</p>
      )}
      {data && draft && (
        <>
          {data.identity_notice && <p role="alert">{data.identity_notice}</p>}
          <div className={styles.fields}>
            <label>
              Cliente
              <select
                aria-label="Cliente"
                value={draft.client_id ?? ""}
                disabled={
                  disabled ||
                  busy ||
                  policy?.read_only ||
                  data.organization_locked
                }
                onChange={(event) =>
                  preparation.setDraft({
                    ...draft,
                    client_id: event.target.value || null,
                    account_id: null,
                  })
                }
              >
                <option value="">Selecione o cliente</option>
                {data.catalog.clients.map((client) => (
                  <option key={client.id} value={client.id}>
                    {client.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Conta
              <select
                aria-label="Conta"
                value={draft.account_id ?? ""}
                disabled={
                  disabled ||
                  busy ||
                  policy?.read_only ||
                  !draft.client_id ||
                  data.organization_locked
                }
                onChange={(event) =>
                  preparation.setDraft({
                    ...draft,
                    account_id: event.target.value || null,
                  })
                }
              >
                <option value="">Sem conta definida</option>
                {data.catalog.accounts
                  .filter((account) => account.client_id === draft.client_id)
                  .map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name || account.bank_code} · final{" "}
                      {account.account_number.slice(-4)}
                    </option>
                  ))}
              </select>
            </label>
            <span className={styles.context}>
              {data.organization_locked
                ? "Cliente e conta deste extrato já estão definidos na Central Financeira."
                : data.automatically_associated
                  ? "Cliente identificado pelos dados do documento. Você pode corrigir aqui."
                  : !data.context.client_id
                    ? "Confira o titular no original. O cliente será associado ao confirmar."
                    : "Cliente e conta serão usados na entrega."}
            </span>
          </div>
          {!data.catalog.clients.length && !policy?.read_only && (
            <p>
              <a href="/clients" target="_blank" rel="noopener noreferrer">
                Cadastrar cliente em outra aba
              </a>{" "}
              <button className="secondary-button" onClick={preparation.retry}>
                Atualizar clientes
              </button>
            </p>
          )}
          {data.matching && (
            <details
              className={styles.matches}
              open={data.matching.matches.length > 0}
            >
              <summary>
                Vínculos com lançamentos{" "}
                {data.matching.matches.length
                  ? `(${data.matching.matches.length})`
                  : ""}
              </summary>
              <button
                className="secondary-button"
                disabled={disabled || busy || policy?.read_only}
                onClick={preparation.retry}
              >
                Procurar vínculos novamente
              </button>
              <p>
                Vínculos documentais não confirmam pagamento nem alteram valores
                do extrato.
              </p>
              {data.matching.notice && (
                <p role="status">{data.matching.notice}</p>
              )}
              {data.matching.partial && (
                <p role="status">
                  A busca está incompleta. Confira os demais documentos antes de
                  concluir a associação.
                </p>
              )}
              {!data.matching.matches.length && !data.matching.partial && (
                <p>
                  Nenhuma correspondência encontrada entre os movimentos
                  disponíveis.
                </p>
              )}
              <ul>
                {data.matching.matches.map((match) => (
                  <li key={match.id}>
                    <div>
                      <strong>{match.description}</strong>
                      <span>
                        {match.date} ·{" "}
                        {new Intl.NumberFormat("pt-BR", {
                          style: "currency",
                          currency: match.currency,
                        }).format(Number(match.amount))}
                      </span>
                      <small>
                        {match.linked
                          ? "Vínculo confirmado"
                          : "Sugestão para conferir"}{" "}
                        · {match.evidence.join(" · ")}
                      </small>
                    </div>
                    <button
                      className="secondary-button"
                      disabled={
                        disabled ||
                        busy ||
                        policy?.read_only ||
                        draft.client_id !== data.context.client_id
                      }
                      onClick={() => void preparation.link(match)}
                    >
                      {match.linked ? "Desfazer vínculo" : "Confirmar vínculo"}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
