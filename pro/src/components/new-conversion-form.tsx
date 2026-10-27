"use client";

import { FilePlus2, LoaderCircle, Upload, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  statementWorkService,
  type StatementWorkService,
} from "@/data/statement-work-service";

import {
  createBatchUploadService,
  type BatchUploadService,
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

  const submit = async () => {
    if (!files.length || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const context = workId ? await workService.get(workId) : null;
      if (context?.state === "completed")
        throw new Error("Reabra o trabalho antes de enviar novos extratos.");
      const batchId =
        createdBatch ?? (await activeService.createAndSubmit(files)).batchId;
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
          <span className="eyebrow">Conversão em lote</span>
          <h1>Nova conversão</h1>
          <p>Envie até 12 arquivos para converter e revisar em conjunto.</p>
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
            multiple
            accept="application/pdf,text/csv,application/x-ofx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,.pdf,.csv,.xlsx,.ofx"
            disabled={submitting || createdBatch !== null}
            onChange={(event) => {
              setFiles(Array.from(event.currentTarget.files ?? []));
              setError(null);
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
