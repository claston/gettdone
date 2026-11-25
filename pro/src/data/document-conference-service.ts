import { createProApiClient, readApiError } from "./pro-api-client";
import type { FinancialDocument } from "@/domain/workspace";

export interface DocumentaryEvidence {
  job_id: string;
  batch_id: string;
  filename: string;
  document_type: string;
  source: string;
  expires_at: string;
  observations: NonNullable<FinancialDocument["observations"]>;
  notice: string;
}
export interface DocumentaryLinks {
  links: Array<{
    id: string;
    version: number;
    state: "confirmed" | "review_required";
    document: DocumentaryEvidence;
  }>;
  unavailable_links: number;
  partial: boolean;
  access_expires_at: string;
}
const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
async function request<T>(
  client: string,
  path: string,
  signal: AbortSignal,
  body?: object,
): Promise<T> {
  const response = await api.request(
    `/api/pro/documents/conference/${encodeURIComponent(client)}${path}`,
    {
      signal,
      cache: "no-store",
      ...(body
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }
        : {}),
    },
  );
  if (!response.ok)
    throw new Error(
      await readApiError(
        response,
        "Não foi possível consultar ou salvar a relação. Atualize e tente novamente.",
      ),
    );
  return response.json();
}
export const conferenceService = {
  document: (client: string, job: string, signal: AbortSignal) =>
    request<DocumentaryEvidence>(
      client,
      `/documents/${encodeURIComponent(job)}`,
      signal,
    ),
  links: (client: string, movement: string, signal: AbortSignal) =>
    request<DocumentaryLinks>(
      client,
      `/movements/${encodeURIComponent(movement)}`,
      signal,
    ),
  confirm: (
    client: string,
    movement: string,
    body: {
      job_id: string;
      source: string;
      movement_version: number;
      operation_id: string;
    },
    signal: AbortSignal,
  ) =>
    request<{ id: string; version: number }>(
      client,
      `/movements/${encodeURIComponent(movement)}/links`,
      signal,
      body,
    ),
  undo: (
    client: string,
    movement: string,
    link: string,
    body: { expected_version: number; operation_id: string },
    signal: AbortSignal,
  ) =>
    request<{ id: string; version: number }>(
      client,
      `/movements/${encodeURIComponent(movement)}/links/${encodeURIComponent(link)}/undo`,
      signal,
      body,
    ),
};
