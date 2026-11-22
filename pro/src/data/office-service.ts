import { createProApiClient, readApiError } from "./pro-api-client";

const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
export type VerificationStatus =
  "pending" | "verified" | "attention" | "unprocessable";
export interface ManualVerification {
  status: VerificationStatus;
  version: number;
  document_type: string | null;
  actor: string | null;
  updated_at: string | null;
}
export interface PackageSelection {
  client_id: string;
  job_ids: string[];
  month: string;
}
export interface PackagePreview {
  snapshot: string;
  items: {
    job_id: string;
    filename?: string;
    included: boolean;
    reason: string | null;
    start_date?: string | null;
    end_date?: string | null;
    cautions?: string[];
    review_url?: string;
  }[];
}
async function request(path: string, body?: unknown, signal?: AbortSignal) {
  const response = await api.request(`/api/pro/documents${path}`, {
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    signal,
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  if (!response.ok)
    throw new Error(
      await readApiError(
        response,
        "Não foi possível concluir. Atualize os documentos e tente novamente.",
      ),
    );
  return response;
}
export const officeService = {
  async verify(
    job: string,
    body: {
      source: string;
      expected_version: number;
      operation_id: string;
      status: VerificationStatus;
      document_type?: string;
    },
  ): Promise<ManualVerification> {
    return (
      await request(`/${encodeURIComponent(job)}/verification`, body)
    ).json();
  },
  async preview(selection: PackageSelection): Promise<PackagePreview> {
    return (await request("/package/preview", selection)).json();
  },
  async download(
    selection: PackageSelection,
    snapshot: string,
    accept_partial: boolean,
  ): Promise<Blob> {
    return (
      await request("/package/download", {
        ...selection,
        snapshot,
        accept_partial,
      })
    ).blob();
  },
  async part(job: string, part: string): Promise<Blob> {
    return (
      await request(
        `/${encodeURIComponent(job)}/parts/${encodeURIComponent(part)}`,
      )
    ).blob();
  },
};

export function saveOfficeFile(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
