import {
  createProApiClient,
  readApiError,
  type ProApiClient,
} from "./pro-api-client";
import type {
  OrganizationAccount,
  OrganizationClient,
  WorkPeriod,
} from "./organization-service";

export interface WorkIssue {
  code: string;
  message: string;
  account_id: string | null;
  job_id: string | null;
}
export interface WorkMovement {
  job_id: string;
  transaction_id: string;
  account_id: string;
  date: string;
  description: string;
  amount: number;
}
export interface WorkEvidence {
  identity_confirmed: boolean;
  start_date?: string | null;
  end_date?: string | null;
  empty_confirmed?: boolean;
  balance?: number | null;
}
export interface WorkDocument {
  job_id: string;
  batch_id: string;
  filename: string;
  account_id: string | null;
  expired: boolean;
  status: string;
  context_version: number;
  reviewed_at: string | null;
  review_url: string | null;
  report_url: string | null;
  report_format?: string;
  source_period?: {
    start_date: string;
    end_date: string;
    document_coverage: boolean;
  } | null;
}
export interface WorkAssessment {
  readiness: string;
  can_complete: boolean;
  documents_count: number;
  accounts_count: number;
  movements_count: number;
  blockers: WorkIssue[];
  reservations: WorkIssue[];
  movements: WorkMovement[];
  coverage: Array<{
    account_id: string;
    status: string;
    documents_count: number;
    gaps: Array<{ start_date: string; end_date: string }>;
    overlaps: Array<{ start_date: string; end_date: string }>;
  }>;
  duplicate_groups: Array<{
    id: string;
    resolved: boolean;
    items: WorkMovement[];
  }>;
}
export interface WorkClosing {
  revision: number;
  created_at: string;
  created_by: string;
  snapshot: {
    conclusion: string;
    client: OrganizationClient;
    period: WorkPeriod;
    accounts?: OrganizationAccount[];
    scope?: { account_ids: string[] };
    remarks: Record<string, string>;
    assessment: Omit<WorkAssessment, "movements" | "duplicate_groups">;
  };
}
export interface StatementWork {
  id: string;
  state: "open" | "completed";
  version: number;
  fingerprint: string;
  client: OrganizationClient;
  period: WorkPeriod;
  accounts: OrganizationAccount[];
  documents: WorkDocument[];
  assessment: WorkAssessment;
  needs_reassessment: boolean;
  settings: {
    account_ids: string[];
    scope_confirmed_at: string | null;
    scope_confirmed_by?: string;
    evidence: Record<string, WorkEvidence>;
    exclusions: Record<string, { reason: string; actor: string; at: string }>;
  };
  closings: WorkClosing[];
  events: Array<{
    event: string;
    created_at: string;
    created_by: string;
    detail: Record<string, unknown>;
  }>;
}
export interface WorkSummary {
  id: string;
  state: string;
  client: OrganizationClient;
  period: WorkPeriod;
  readiness: string;
  needs_reassessment: boolean;
  accounts_count: number;
  documents_count: number;
  blockers_count: number;
  reservations_count: number;
  conclusion: string | null;
}
export function createStatementWorkService(apiClient?: ProApiClient) {
  const client =
    apiClient ??
    createProApiClient({ apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/" });
  async function read<T>(
    path: string,
    body?: unknown,
    method = "POST",
  ): Promise<T> {
    const response = await client.request(
      `/api/pro/works${path}`,
      body === undefined
        ? { cache: "no-store" }
        : {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
    );
    if (!response.ok)
      throw new Error(
        await readApiError(
          response,
          "Não foi possível carregar ou salvar o trabalho.",
        ),
      );
    return response.json() as Promise<T>;
  }
  const route = (id: string) => `/${encodeURIComponent(id)}`;
  return {
    attachBatch: (w: StatementWork, batchId: string) =>
      read<StatementWork>(
        `${route(w.id)}/batches/${encodeURIComponent(batchId)}`,
        { expected_version: w.version },
      ),
    list: (
      filters: { client: string; period: string; state: string },
      offset = 0,
    ) => {
      const q = new URLSearchParams({ offset: String(offset) });
      if (filters.client) q.set("client_id", filters.client);
      if (filters.period) q.set("period_id", filters.period);
      if (filters.state) q.set("state", filters.state);
      return read<{ items: WorkSummary[]; next_offset: number | null }>(
        `?${q}`,
      );
    },
    get: (id: string) => read<StatementWork>(route(id)),
    create: (client_id: string, period_id: string) =>
      read<StatementWork>("", { client_id, period_id }),
    scope: (w: StatementWork, account_ids: string[]) =>
      read<StatementWork>(
        `${route(w.id)}/scope`,
        { expected_version: w.version, account_ids },
        "PUT",
      ),
    attach: (
      w: StatementWork,
      job_id: string,
      account_id: string | null,
      context_version: number,
    ) =>
      read<StatementWork>(`${route(w.id)}/documents`, {
        expected_version: w.version,
        job_id,
        account_id,
        context_version,
      }),
    evidence: (w: StatementWork, job: string, evidence: WorkEvidence) =>
      read<StatementWork>(
        `${route(w.id)}/documents/${encodeURIComponent(job)}/evidence`,
        { expected_version: w.version, ...evidence },
        "PUT",
      ),
    exclude: (
      w: StatementWork,
      job: string,
      reason: string,
      excluded: boolean,
    ) =>
      read<StatementWork>(
        `${route(w.id)}/documents/${encodeURIComponent(job)}/exclusion`,
        { expected_version: w.version, reason, excluded },
        "PUT",
      ),
    distinct: (w: StatementWork, group: string, reason: string) =>
      read<StatementWork>(
        `${route(w.id)}/duplicates/${encodeURIComponent(group)}`,
        { expected_version: w.version, reason },
        "PUT",
      ),
    complete: (w: StatementWork, remarks: Record<string, string>) =>
      read<StatementWork>(`${route(w.id)}/complete`, {
        expected_version: w.version,
        expected_fingerprint: w.fingerprint,
        remarks,
      }),
    reopen: (w: StatementWork, reason: string) =>
      read<StatementWork>(`${route(w.id)}/reopen`, {
        expected_version: w.version,
        reason,
      }),
  };
}
export type StatementWorkService = ReturnType<
  typeof createStatementWorkService
>;
export const statementWorkService = createStatementWorkService();
