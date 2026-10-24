import {
  createProApiClient,
  readApiError,
  type ProApiClient,
} from "./pro-api-client";

export interface OrganizationClient {
  id: string;
  name: string;
  tax_id: string | null;
  trade_name: string;
}
export interface OrganizationAccount {
  id: string;
  client_id: string;
  name: string;
  bank_code: string;
  branch: string;
  account_number: string;
}
export interface WorkPeriod {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
}
export interface OrganizationCatalog {
  clients: OrganizationClient[];
  accounts: OrganizationAccount[];
  periods: WorkPeriod[];
}
export interface ConversionContext {
  client_id: string | null;
  account_id: string | null;
  period_ids: string[];
  version: number;
}
export interface OrganizedConversion {
  job_id: string;
  batch_id: string;
  filename: string;
  status: string;
  is_expired: boolean;
  created_at: string;
  movement_start_date: string | null;
  movement_end_date: string | null;
  review_url: string | null;
  context: ConversionContext;
}
export interface OrganizationFilters {
  client: string;
  period: string;
  account: string;
}
export interface OrganizationPageResult {
  items: OrganizedConversion[];
  next_cursor: string | null;
}
export interface OrganizationService {
  catalog(signal?: AbortSignal): Promise<OrganizationCatalog>;
  listConversions(
    filters: OrganizationFilters,
    cursor?: string,
    signal?: AbortSignal,
  ): Promise<OrganizationPageResult>;
  saveClient(
    client: Omit<OrganizationClient, "id">,
    id?: string,
  ): Promise<OrganizationClient>;
  saveAccount(
    account: Omit<OrganizationAccount, "id">,
    id?: string,
  ): Promise<OrganizationAccount>;
  savePeriod(period: Omit<WorkPeriod, "id">, id?: string): Promise<WorkPeriod>;
  organize(
    batchId: string,
    jobId: string,
    context: ConversionContext,
  ): Promise<ConversionContext>;
}

export function createOrganizationService(
  options: { apiClient?: ProApiClient } = {},
): OrganizationService {
  const client =
    options.apiClient ??
    createProApiClient({ apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/" });
  const base = "/api/pro/organization";
  async function read<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await client.request(`${base}${path}`, init);
    if (!response.ok)
      throw new Error(
        await readApiError(
          response,
          "Não foi possível carregar a organização de conversões.",
        ),
      );
    return response.json() as Promise<T>;
  }
  function save<T>(kind: string, value: unknown, id?: string) {
    return read<T>(`/${kind}${id ? `/${encodeURIComponent(id)}` : ""}`, {
      method: id ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(value),
    });
  }
  return {
    catalog: (signal) =>
      read<OrganizationCatalog>("/catalog", { signal, cache: "no-store" }),
    listConversions(filters, cursor, signal) {
      const query = new URLSearchParams();
      if (filters.client === "unassigned") query.set("unassigned", "true");
      else if (filters.client) query.set("client_id", filters.client);
      if (filters.period) query.set("period_id", filters.period);
      if (filters.account) query.set("account_id", filters.account);
      if (cursor) query.set("cursor", cursor);
      return read<OrganizationPageResult>(`/conversions?${query}`, {
        signal,
        cache: "no-store",
      });
    },
    saveClient: (value, id) => save<OrganizationClient>("clients", value, id),
    saveAccount: (value, id) =>
      save<OrganizationAccount>("accounts", value, id),
    savePeriod: (value, id) => save<WorkPeriod>("periods", value, id),
    organize(batchId, jobId, context) {
      return read<ConversionContext>(
        `/batches/${encodeURIComponent(batchId)}/jobs/${encodeURIComponent(jobId)}/context`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            client_id: context.client_id,
            account_id: context.account_id,
            period_ids: context.period_ids,
            expected_version: context.version,
          }),
        },
      );
    },
  };
}

export const organizationService = createOrganizationService();
