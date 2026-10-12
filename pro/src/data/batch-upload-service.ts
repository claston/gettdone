import {
  createProApiClient,
  readApiError,
  type ProApiClient,
} from "./pro-api-client";

export interface BatchUploadService {
  createAndSubmit(files: File[]): Promise<{ batchId: string }>;
}

interface BatchUploadServiceOptions {
  apiUrl: string;
  fetcher?: typeof fetch;
  digestFile?: (file: File) => Promise<string>;
  createIdempotencyKey?: () => string;
  apiClient?: ProApiClient;
}

interface ConversionRuntime {
  direct_batch_enabled: boolean;
  upload_mode: string;
  execution_mode: string;
  batch_max_files: number;
}

interface PreparedUpload {
  job_id: string;
  filename: string;
  url: string;
  fields: Record<string, string>;
}

interface PreparedBatch {
  batch_id: string;
  uploads?: PreparedUpload[] | null;
}

export function createBatchUploadService(
  options: BatchUploadServiceOptions,
): BatchUploadService {
  const apiUrl = options.apiUrl.replace(/\/+$/, "");
  const fetcher = options.fetcher ?? fetch;
  const apiClient =
    options.apiClient ?? createProApiClient({ apiUrl, fetcher });
  const digestFile = options.digestFile ?? sha256File;
  const createIdempotencyKey =
    options.createIdempotencyKey ?? secureIdempotencyKey;

  return {
    async createAndSubmit(files: File[]) {
      if (!files.length || files.length > 12) {
        throw new Error("Selecione entre 1 e 12 arquivos.");
      }

      const runtimeResponse = await apiClient.request(
        "/api/conversion-runtime",
      );
      if (!runtimeResponse.ok) {
        throw new Error(
          await readApiError(
            runtimeResponse,
            "Não foi possível consultar o processamento em lote.",
          ),
        );
      }
      const runtime = (await runtimeResponse.json()) as ConversionRuntime;
      if (
        !runtime.direct_batch_enabled ||
        runtime.upload_mode !== "direct_s3" ||
        runtime.execution_mode !== "sqs_lambda"
      ) {
        throw new Error(
          "O processamento assíncrono está indisponível. Nenhum arquivo foi enviado.",
        );
      }
      if (files.length > runtime.batch_max_files) {
        throw new Error(
          `Este ambiente permite no máximo ${runtime.batch_max_files} arquivo(s) por lote.`,
        );
      }

      const fileContracts = await Promise.all(
        files.map(async (file) => ({
          filename: file.name,
          content_type: contentTypeForFile(file),
          size_bytes: file.size,
          sha256_hex: await digestFile(file),
        })),
      );
      const createdResponse = await apiClient.request(
        "/api/pro/conversion-batches",
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": createIdempotencyKey(),
          },
          body: JSON.stringify({ files: fileContracts }),
        },
      );
      if (!createdResponse.ok) {
        throw new Error(
          await readApiError(
            createdResponse,
            "Não foi possível criar o lote de conversão.",
          ),
        );
      }
      const prepared = (await createdResponse.json()) as PreparedBatch;
      const uploads = prepared.uploads ?? [];
      if (uploads.length !== files.length) {
        throw new Error(
          "O backend não retornou instruções para todos os arquivos.",
        );
      }

      await Promise.all(
        files.map(async (file, index) => {
          const upload = uploads[index];
          const form = new FormData();
          Object.entries(upload.fields).forEach(([key, value]) =>
            form.append(key, value),
          );
          form.append("file", file);
          const response = await fetcher(upload.url, {
            method: "POST",
            body: form,
          });
          if (!response.ok) {
            throw new Error(
              `Não foi possível enviar o arquivo ${file.name} ao armazenamento seguro.`,
            );
          }
        }),
      );

      const submittedResponse = await apiClient.request(
        `/api/pro/conversion-batches/${encodeURIComponent(prepared.batch_id)}/submit`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
          },
          body: "{}",
        },
      );
      if (!submittedResponse.ok) {
        throw new Error(
          await readApiError(
            submittedResponse,
            "Os arquivos foram enviados, mas o lote não entrou na fila.",
          ),
        );
      }
      return { batchId: prepared.batch_id };
    },
  };
}

function contentTypeForFile(file: File): string {
  const extension = file.name.split(".").pop()?.toLowerCase();
  const contentType = extension
    ? {
        csv: "text/csv",
        ofx: "application/x-ofx",
        pdf: "application/pdf",
        xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }[extension]
    : undefined;
  if (!contentType) {
    throw new Error(
      `O formato do arquivo ${file.name} não é compatível. Use PDF, CSV, XLSX ou OFX.`,
    );
  }
  return contentType;
}

async function sha256File(file: File): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error(
      "Seu navegador não oferece o recurso seguro necessário para o upload.",
    );
  }
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    await file.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

function secureIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  if (!globalThis.crypto?.getRandomValues) {
    throw new Error("Não foi possível gerar uma chave segura para o lote.");
  }
  return `batch-${Array.from(
    globalThis.crypto.getRandomValues(new Uint32Array(4)),
  )
    .map((value) => value.toString(16).padStart(8, "0"))
    .join("")}`;
}
