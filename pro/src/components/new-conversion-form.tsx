"use client";

import { FilePlus2, LoaderCircle, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import styles from "./new-conversion-form.module.css";
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
  importContext,
  onClose,
}: {
  service?: BatchUploadService;
  workId?: string;
  workService?: StatementWorkService;
  importContext?: { clientId?: string; clientName?: string };
  onClose?(): void;
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
  const [selectedProMode, setProMode] = useState(false);
  const importing = Boolean(importContext);
  const proMode = !importing && selectedProMode;
  const [proOptionsFailed, setProOptionsFailed] = useState(false);
  const [proOptionsRetrying, setProOptionsRetrying] = useState(false);
  const [proOptionsAttempt, setProOptionsAttempt] = useState(0);
  const [proEstimate, setProEstimate] = useState<ProConversionEstimate | null>(
    null,
  );
  useEffect(() => {
    if (importing) return;
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
  }, [activeService, proOptionsAttempt, importing]);

  const selectFiles = (selected: File[]) => {
    if (submitting || createdBatch !== null || !selected.length) return;
    setFiles(selected);
    setError(null);
    setProEstimate(null);
  };

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
      if (!batchId) {
        const result = importContext?.clientId
          ? await activeService.createAndSubmit(files, {
              clientId: importContext.clientId,
            })
          : await activeService.createAndSubmit(files);
        batchId = result.batchId;
      }
      if (importContext) {
        setCreatedBatch(batchId);
        const query = new URLSearchParams({ batch: batchId });
        if (importContext.clientId) query.set("client", importContext.clientId);
        query.set("view", "delivery");
        router.push(
          `/review/${encodeURIComponent(batchId)}?return_to=${encodeURIComponent(`/documents?${query}`)}`,
        );
        return;
      }
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
          : importing
            ? "Não foi possível importar os documentos. Tente novamente."
            : "Não foi possível criar o lote.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const Container = importContext ? "section" : "main";
  return (
    <Container
      className={
        importing ? styles.panel : "standard-page conversion-upload-page"
      }
      aria-label={importing ? "Importação de documentos" : undefined}
    >
      {importing ? (
        <header className={styles.heading}>
          <div>
            <h2>Importar documentos</h2>
            {importContext?.clientName && (
              <p>Cliente selecionado: {importContext.clientName}</p>
            )}
          </div>
          {onClose && (
            <button
              type="button"
              className="secondary-button"
              aria-label="Fechar importação"
              disabled={submitting}
              onClick={onClose}
            >
              Fechar
            </button>
          )}
        </header>
      ) : (
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
      )}

      <div className={importing ? styles.content : "upload-card"}>
        {!importing && proOptionsFailed && (
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
        {!importing && proOptions?.enabled && (
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
        {!importing && (
          <div className="upload-card-heading">
            <span className="placeholder-icon">
              <Upload size={24} />
            </span>
            <div>
              <h2>Selecione os extratos</h2>
              <p>PDF, CSV, XLSX ou OFX.</p>
            </div>
          </div>
        )}

        <label
          className={`upload-dropzone ${importing ? styles.dropzone : ""}`}
          onDragOver={importing ? (event) => event.preventDefault() : undefined}
          onDrop={
            importing
              ? (event) => {
                  event.preventDefault();
                  selectFiles(Array.from(event.dataTransfer.files));
                }
              : undefined
          }
        >
          <FilePlus2 size={28} />
          {importing && <strong>Arraste os arquivos para cá</strong>}
          <strong>
            {importing ? "ou Escolher arquivos" : "Escolher arquivos"}
          </strong>
          {importing && <span>PDF, CSV, XLSX ou OFX · Até 12 arquivos</span>}
          <span aria-live="polite">
            {files.length
              ? `${files.length} ${files.length === 1 ? "arquivo selecionado" : "arquivos selecionados"}`
              : importing
                ? ""
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
              selectFiles(Array.from(event.currentTarget.files ?? []));
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
        {!importing && proEstimate && (
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

        {importing && (
          <p className={styles.guidance}>
            {importContext?.clientId
              ? "Os arquivos serão associados a este cliente. Após a leitura, confira o titular e os dados dos documentos."
              : "Após a leitura, confira o titular e confirme a associação dos documentos ao cliente."}
          </p>
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
              ? importing
                ? "Importando documentos…"
                : "Preparando lote…"
              : createdBatch
                ? importContext
                  ? "Ver documentos enviados"
                  : "Retomar vínculo com o trabalho"
                : proMode
                  ? proEstimate
                    ? "Iniciar Conversão PRO"
                    : "Verificar Conversão PRO"
                  : importContext
                    ? "Importar documentos"
                    : "Criar lote"}
          </button>
        </div>
      </div>
    </Container>
  );
}

function formatFileSize(size: number): string {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`;
}
