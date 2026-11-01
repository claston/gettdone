"use client";

import { FilePlus2, LoaderCircle, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  statementWorkService,
  type StatementWorkService,
} from "@/data/statement-work-service";

import {
  createBatchUploadService,
  type BatchUploadService,
  type ProConversionOptions,
  type ProConversionEstimate,
} from "@/data/batch-upload-service";

export function NewConversionForm({
  service,
  workId,
  workService = statementWorkService,
}: {
  service?: BatchUploadService;
  workId?: string;
  workService?: StatementWorkService;
}) {
  const router = useRouter();
  const defaultService = useMemo(
    () =>
      createBatchUploadService({
        apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
      }),
    [],
  );
  const activeService = service ?? defaultService;
  const [files, setFiles] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdBatch, setCreatedBatch] = useState<string | null>(null);
  const [proOptions, setProOptions] = useState<ProConversionOptions | null>(
    null,
  );
  const [proMode, setProMode] = useState(false);
  const [proOptionsFailed, setProOptionsFailed] = useState(false);
  const [proOptionsRetrying, setProOptionsRetrying] = useState(false);
  const [proOptionsAttempt, setProOptionsAttempt] = useState(0);
  const [proEstimate, setProEstimate] = useState<ProConversionEstimate | null>(
    null,
  );
  useEffect(() => {
    let active = true;
    activeService
      .getProOptions?.()
      .then((value) => {
        if (active) {
          setProOptions(value);
          setProOptionsFailed(false);
        }
      })
      .catch(() => {
        if (active) setProOptionsFailed(true);
      })
      .finally(() => {
        if (active) setProOptionsRetrying(false);
      });
    return () => {
      active = false;
    };
  }, [activeService, proOptionsAttempt]);

  const submit = async () => {
    if (!files.length || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const context = workId ? await workService.get(workId) : null;
      if (context?.state === "completed")
        throw new Error("Reabra o trabalho antes de enviar novos extratos.");
      let batchId = createdBatch;
      if (!batchId && proMode) {
        if (
          files.length !== 1 ||
          !files[0].name.toLowerCase().endsWith(".pdf")
        ) {
          throw new Error(
            "A Conversão PRO aceita um único PDF por solicitação.",
          );
        }
        if (
          !activeService.preparePro ||
          !activeService.submitPro ||
          !proOptions?.eligible
        ) {
          throw new Error("A Conversão PRO está indisponível para esta conta.");
        }
        if (!proEstimate) {
          setProEstimate(await activeService.preparePro(files[0]));
          return;
        }
        batchId = (await activeService.submitPro(proEstimate.batchId)).batchId;
      }
      batchId ??= (await activeService.createAndSubmit(files)).batchId;
      if (context) {
        setCreatedBatch(batchId);
        await workService.attachBatch(context, batchId);
      }
      router.push(
        `/review/${encodeURIComponent(batchId)}${workId ? `?return_to=${encodeURIComponent(`/works/${workId}`)}` : ""}`,
      );
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível criar o lote.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="standard-page conversion-upload-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">
            {proMode ? "Conversão PRO" : "Conversão em lote"}
          </span>
          <h1>Nova conversão</h1>
          <p>
            {proMode
              ? "Envie um PDF digitalizado para análise de tabelas e revisão."
              : "Envie até 12 arquivos para converter e revisar em conjunto."}
          </p>
          {workId && (
            <p>
              Os arquivos serão vinculados ao trabalho.{" "}
              <a href={`/works/${encodeURIComponent(workId)}`}>
                Voltar ao trabalho
              </a>
            </p>
          )}
        </div>
      </div>

      <section className="upload-card">
        {proOptionsFailed && (
          <div role="status">
            <p>
              Não foi possível consultar a disponibilidade da Conversão PRO.
            </p>
            <button
              type="button"
              className="secondary-button"
              aria-label="Consultar a Conversão PRO novamente"
              disabled={proOptionsRetrying}
              onClick={() => {
                setProOptionsRetrying(true);
                setProOptionsAttempt((attempt) => attempt + 1);
              }}
            >
              {proOptionsRetrying ? "Consultando…" : "Tentar novamente"}
            </button>
          </div>
        )}
        {proOptions?.enabled && (
          <fieldset disabled={submitting || createdBatch !== null}>
            <legend>Tipo de conversão</legend>
            <label>
              <input
                type="radio"
                name="conversion-mode"
                checked={!proMode}
                onChange={() => {
                  setProMode(false);
                  setProEstimate(null);
                }}
              />{" "}
              Padrão
            </label>{" "}
            <label>
              <input
                type="radio"
                name="conversion-mode"
                checked={proMode}
                disabled={
                  !proOptions.eligible || proOptions.remaining_pages < 1
                }
                onChange={() => {
                  setProMode(true);
                  setProEstimate(null);
                  setError(null);
                }}
              />{" "}
              Conversão PRO
            </label>
            <p>
              {proOptions.eligible
                ? `${proOptions.remaining_pages} de ${proOptions.monthly_pages} páginas PRO disponíveis neste ciclo.`
                : "Disponível somente para assinaturas pagas ativas."}
            </p>
            {proOptions.eligible && proOptions.reset_at && (
              <p>
                Renovação do limite PRO:{" "}
                {new Date(proOptions.reset_at).toLocaleString("pt-BR")}.
              </p>
            )}
          </fieldset>
        )}
        {proMode && (
          <p>
            Piloto para extratos Asaas digitalizados. Um PDF por vez, até{" "}
            {proOptions?.max_pages} páginas. Pode levar mais tempo e continuar
            em revisão. As páginas do PDF contam para o limite PRO quando a
            leitura é solicitada, mesmo se a conversão não for concluída.
          </p>
        )}
        <div className="upload-card-heading">
          <span className="placeholder-icon">
            <Upload size={24} />
          </span>
          <div>
            <h2>Selecione os extratos</h2>
            <p>
              PDF, CSV, XLSX ou OFX. Cada arquivo será enviado diretamente ao
              armazenamento seguro.
            </p>
          </div>
        </div>

        <label className="upload-dropzone">
          <FilePlus2 size={28} />
          <strong>Escolher arquivos</strong>
          <span aria-live="polite">
            {files.length
              ? `${files.length} ${files.length === 1 ? "arquivo selecionado" : "arquivos selecionados"}`
              : "Nenhum arquivo selecionado"}
          </span>
          <input
            aria-label="Selecionar arquivos"
            type="file"
            multiple={!proMode}
            accept={
              proMode
                ? "application/pdf,.pdf"
                : "application/pdf,text/csv,application/x-ofx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.pdf,.csv,.xlsx,.ofx"
            }
            disabled={submitting || createdBatch !== null}
            onChange={(event) => {
              setFiles(Array.from(event.currentTarget.files ?? []));
              setError(null);
              setProEstimate(null);
              event.currentTarget.value = "";
            }}
          />
        </label>

        {files.length > 0 && (
          <ul className="upload-file-list" aria-label="Arquivos selecionados">
            {files.map((file, index) => (
              <li
                key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
              >
                <span>{file.name}</span>
                <div className="upload-file-actions">
                  <small>{formatFileSize(file.size)}</small>
                  <button
                    type="button"
                    className="secondary-button upload-file-remove"
                    aria-label={`Remover ${file.name}`}
                    disabled={submitting || createdBatch !== null}
                    onClick={() => {
                      setFiles((current) =>
                        current.filter((_, fileIndex) => fileIndex !== index),
                      );
                      setError(null);
                      setProEstimate(null);
                    }}
                  >
                    <X size={14} /> Remover
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {error && (
          <p className="upload-error" role="alert">
            {error}
          </p>
        )}
        {proEstimate && (
          <div role="status">
            <p>{proEstimate.pages} páginas serão processadas.</p>
            <p>
              Consome {proEstimate.pages} páginas da franquia do plano e do
              limite PRO. Restarão {proEstimate.remaining_after} páginas PRO
              neste ciclo.
            </p>
            <p>Confirme para iniciar a Conversão PRO.</p>
          </div>
        )}

        <div className="upload-actions">
          <button
            type="button"
            className="primary-button"
            disabled={!files.length || submitting}
            onClick={() => void submit()}
          >
            {submitting ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Upload size={16} />
            )}
            {submitting
              ? "Preparando lote…"
              : createdBatch
                ? "Retomar vínculo com o trabalho"
                : proMode
                  ? proEstimate
                    ? "Iniciar Conversão PRO"
                    : "Verificar Conversão PRO"
                  : "Criar lote"}
          </button>
        </div>
      </section>
    </main>
  );
}

function formatFileSize(size: number): string {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`;
}
