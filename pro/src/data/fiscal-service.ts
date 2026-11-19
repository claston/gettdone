import { createProApiClient, readApiError } from "./pro-api-client";

export interface FiscalNote {
  id: string;
  version: number;
  kind: "nfe" | "nfse";
  number: string | null;
  series: string | null;
  issued_on: string | null;
  competence_on: string | null;
  gross_amount: string | null;
  net_amount: string | null;
  operation: string;
  status: string;
  direction: "issued" | "received" | "unconfirmed";
  issuer: { name: string | null; tax_id: string | null };
  recipient: { name: string | null; tax_id: string | null };
  taxes: Record<string, string>;
  review_required: boolean;
  source: {
    format: string;
    fields: Record<string, { page: number; line: number }>;
  };
}
export interface FiscalMovement {
  id: string;
  date: string;
  amount: string;
  currency: string;
  description: string;
  version: number;
  possible_duplicate: boolean;
  classification: {
    category: string;
    state: string;
    version: number;
    details: Record<string, string>;
  } | null;
  tax_suggestion: { category: string; evidence: string } | null;
  source: { job_id: string; batch_id: string; filename: string };
}
export interface FiscalWorkspaceData {
  classification_shadow_enabled?: boolean;
  features: { invoices: boolean; taxes: boolean; revenue: boolean };
  documents: FiscalNote[];
  imports: Array<{
    id: string;
    state: string;
    error: string | null;
    filename: string;
  }>;
  movements: FiscalMovement[];
  movement_count: number;
  next_offset: number | null;
  links: Array<{
    id: string;
    document_id: string;
    movement_id: string;
    amount: string;
    movement_description: string;
    movement_date: string;
    currency: string;
    version: number;
    state: string;
  }>;
  suggestions: Array<{
    movement_id: string;
    movement_version: number;
    amount: string;
    confidence: string;
    evidence: string[];
    movement: FiscalMovement;
  }>;
  categories: Record<string, string>;
  access_expires_at: string | null;
  truncated: boolean;
  summary: {
    documented_sales: string | null;
    operating_receipts: string | null;
    unclassified_inflows: string | null;
    excluded_inflows: string | null;
    tax_payments: Record<string, string | null>;
    pending_documents: number;
    coverage: string;
    consolidation: string;
    basis: string;
  };
}
const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
const base = (client: string) =>
  `/api/pro/financial-center/clients/${encodeURIComponent(client)}`;
async function checked(path: string, init?: RequestInit) {
  const result = await api.request(path, { ...init, cache: "no-store" });
  if (!result.ok)
    throw new Error(
      await readApiError(
        result,
        "Não foi possível concluir a operação fiscal.",
      ),
    );
  return result;
}
export async function loadFiscalWorkspace(
  client: string,
  query: string,
  signal: AbortSignal,
): Promise<FiscalWorkspaceData> {
  return (await checked(`${base(client)}/fiscal?${query}`, { signal })).json();
}
export async function fiscalCommand(
  client: string,
  path: string,
  body: object,
) {
  return (
    await checked(`${base(client)}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  ).json();
}
export async function uploadFiscalNote(client: string, file: File) {
  if (
    !/\.(xml|pdf)$/i.test(file.name) ||
    file.size === 0 ||
    file.size > 5 * 1024 * 1024
  )
    throw new Error("Envie uma nota XML ou PDF de até 5 MB.");
  const digest = Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", await file.arrayBuffer()),
    ),
  )
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
  const prepared = await fiscalCommand(client, "/fiscal/imports", {
    filename: file.name,
    size_bytes: file.size,
    digest,
  });
  if (prepared.upload) {
    const form = new FormData();
    Object.entries(prepared.upload.fields as Record<string, string>).forEach(
      ([key, value]) => form.append(key, value),
    );
    form.append("file", file);
    const response = await fetch(prepared.upload.url, {
      method: "POST",
      body: form,
      credentials: "omit",
    });
    if (!response.ok)
      throw new Error(
        "Não foi possível enviar a nota. Selecione o arquivo para tentar novamente.",
      );
    await fiscalCommand(
      client,
      `/fiscal/imports/${encodeURIComponent(prepared.id)}/submit`,
      {},
    );
  }
}
export async function downloadFiscal(
  client: string,
  path: string,
  filename: string,
) {
  const response = await checked(`${base(client)}/fiscal/${path}`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
