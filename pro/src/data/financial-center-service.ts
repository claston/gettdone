import { createProApiClient, readApiError } from "./pro-api-client";

export interface FinancialDay {
  date: string;
  count: number;
  pending_count: number;
  coverage: "unknown" | "no_activity_confirmed";
  currency: string | null;
  inflows: string | null;
  outflows: string | null;
  consolidation: "observed" | "review_required";
}
export interface CanonicalMovement {
  id: string;
  date: string;
  amount: string;
  currency: string;
  description: string;
  account_id: string | null;
  version: number;
  review_state: "pending" | "reviewed";
  pending: boolean;
  possible_duplicate: boolean;
  source: {
    analysis_id: string;
    job_id: string;
    batch_id: string;
    filename: string;
    page: number | null;
    analysis_version: number;
    availability: "available";
  };
}
export interface FinancialMonth {
  client: { id: string; name: string; tax_id: string | null };
  month: string;
  accounts: Array<{ id: string; label: string }>;
  days: FinancialDay[];
  movements: CanonicalMovement[];
  next_cursor: string | null;
  coverage: "unknown";
  financial_reconciliation: "not_implemented";
  unavailable_sources: number;
  legacy_sources: number;
  access_expires_at: string | null;
}
const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
export async function loadFinancialMonth(
  clientId: string,
  query: string,
  signal: AbortSignal,
): Promise<FinancialMonth> {
  const response = await api.request(
    `/api/pro/financial-center/clients/${encodeURIComponent(clientId)}?${query}`,
    {
      signal,
      cache: "no-store",
    },
  );
  if (!response.ok)
    throw new Error(
      await readApiError(response, "Não foi possível consultar os movimentos."),
    );
  return response.json();
}

// Money stays in integer cents through aggregation and presentation.
export function decimalCents(value: string): bigint {
  if (!/^-?\d+(\.\d{1,2})?$/.test(value))
    throw new Error("Valor monetário inválido.");
  const [whole, fraction = ""] = value.replace("-", "").split(".");
  return (
    (BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"))) *
    (value.startsWith("-") ? -1n : 1n)
  );
}
export function financialMoney(value: bigint, currency: string): string {
  const absolute = value < 0n ? -value : value;
  const parts = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency,
  }).formatToParts(absolute / 100n);
  return (
    (value < 0n ? "− " : "") +
    parts
      .map((part) =>
        part.type === "fraction"
          ? String(absolute % 100n).padStart(2, "0")
          : part.value,
      )
      .join("")
  );
}
