"use client";

import { useEffect, useState } from "react";
import {
  shadowRequest,
  type ShadowRun,
  type ShadowSource,
} from "@/data/classification-shadow";
import styles from "./classification-shadow.module.css";

export function ClassificationShadow({
  clientId,
  sources,
}: {
  clientId: string;
  sources: ShadowSource[];
}) {
  const [selected, setSelected] = useState("");
  const source = sources.find((row) => `${row.kind}:${row.id}` === selected);
  if (!sources.length) return null;
  return (
    <details className={styles.panel}>
      <summary>Avaliar classificação experimental</summary>
      <p>
        Confira sugestões e registre sua avaliação. Elas não alteram
        lançamentos, totais ou arquivos exportados.
      </p>
      <label>
        Documento ou lançamento
        <select
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="">Selecione para avaliar</option>
          {sources.map((row) => (
            <option
              key={`${row.kind}:${row.id}`}
              value={`${row.kind}:${row.id}`}
            >
              {row.label}
            </option>
          ))}
        </select>
      </label>
      {source && (
        <Evaluation
          key={`${clientId}:${source.kind}:${source.id}:${source.version}`}
          clientId={clientId}
          source={source}
        />
      )}
    </details>
  );
}

function Evaluation({
  clientId,
  source,
}: {
  clientId: string;
  source: ShadowSource;
}) {
  const [run, setRun] = useState<ShadowRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    if (!run) return;
    const abort = new AbortController();
    const expiry = window.setTimeout(
      () => setExpired(true),
      Math.max(0, Date.parse(run.expires_at) - Date.now()),
    );
    const timer =
      run.state === "queued" && !run.stale
        ? window.setTimeout(() => {
            void shadowRequest(
              clientId,
              `/runs/${run.id}`,
              undefined,
              abort.signal,
            )
              .then(setRun)
              .catch((caught) => {
                if (!abort.signal.aborted)
                  setError(
                    caught instanceof Error
                      ? caught.message
                      : "Consulta indisponível.",
                  );
              });
          }, 3000)
        : undefined;
    return () => {
      abort.abort();
      window.clearTimeout(timer);
      window.clearTimeout(expiry);
    };
  }, [clientId, run]);
  async function execute(path: string, body?: object) {
    setBusy(true);
    setError("");
    try {
      setRun(await shadowRequest(clientId, path, body));
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Avaliação indisponível.",
      );
    } finally {
      setBusy(false);
    }
  }
  if (expired)
    return (
      <p role="status">
        Esta avaliação expirou. Atualize os documentos disponíveis.
      </p>
    );
  return (
    <div>
      <button
        className="secondary-button"
        disabled={busy || run?.state === "queued"}
        onClick={() =>
          void execute("/runs", {
            source_kind: source.kind,
            source_id: source.id,
            source_version: source.version,
          })
        }
      >
        Avaliar classificação
      </button>
      {error && <p role="alert">{error}</p>}
      {error && run?.state === "queued" && (
        <button disabled={busy} onClick={() => void execute(`/runs/${run.id}`)}>
          Tentar consultar novamente
        </button>
      )}
      {run?.state === "queued" && (
        <p role="status">Processando a avaliação fiscal…</p>
      )}
      {run?.state === "failed" && (
        <p role="alert">
          O processamento não foi concluído. A conversão continua disponível.
        </p>
      )}
      {run?.stale && (
        <p role="alert">
          Os dados de origem mudaram. Atualize para fazer uma nova avaliação.
        </p>
      )}
      {run && !run.stale && run.state === "ready" && (
        <>
          <p>
            Concordâncias: {run.metrics.agree} · Divergências:{" "}
            {run.metrics.disagree} · A conferir: {run.metrics.unknown}
          </p>
          {run.items.map((item) => (
            <article key={item.id} className={styles.item}>
              <h4>{item.description || `Item ${item.id}`}</h4>
              {item.original && (
                <p>
                  Informado no XML: NCM {item.original.ncm || "ausente"} · CST{" "}
                  {item.original.cst || "ausente"} · Classificação{" "}
                  {item.original.cClassTrib || "ausente"}
                </p>
              )}
              <p>
                <strong>{item.label}</strong>
              </p>
              <p>{item.evidence}</p>
              {item.reported_code_check && (
                <p>
                  {(
                    {
                      compatible_requires_review:
                        "Códigos compatíveis; condições da operação ainda precisam de revisão.",
                      context_required:
                        "O código informado exige contexto adicional.",
                      incompatible:
                        "Há incompatibilidade entre os códigos informados.",
                      invalid_classification:
                        "Classificação informada não encontrada para a data consultada.",
                    } as Record<string, string>
                  )[item.reported_code_check] || "Conferência pendente."}
                </p>
              )}
              {item.candidates.length > 0 && (
                <ul>
                  {item.candidates.map((candidate) => (
                    <li key={candidate.code}>
                      <strong>
                        {candidate.code} · CST {candidate.cst}
                      </strong>{" "}
                      — {candidate.label}
                      {candidate.foundations.map((text, index) => (
                        <p key={index}>{text}</p>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
              <div
                className={styles.actions}
                role="group"
                aria-label={`Avaliação de ${item.description || item.id}`}
              >
                {(
                  [
                    ["agree", "Concordo"],
                    ["disagree", "Discordo"],
                    ["unknown", "Não sei"],
                  ] as const
                ).map(([verdict, label]) => (
                  <button
                    key={verdict}
                    disabled={busy}
                    aria-pressed={run.feedback.some(
                      (row) =>
                        row.item_id === item.id && row.verdict === verdict,
                    )}
                    onClick={() =>
                      void execute(`/runs/${run.id}/feedback`, {
                        item_id: item.id,
                        verdict,
                      })
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>
            </article>
          ))}
          <details>
            <summary>Origem das regras</summary>
            <p>{run.engine_version}</p>
          </details>
        </>
      )}
    </div>
  );
}
