import { createProApiClient, readApiError } from "./pro-api-client";
import type { OrganizedConversion } from "./organization-service";

export type DocumentCategory =
  "all" | "statements" | "invoices" | "receipts" | "unclassified";
export interface DocumentItem extends OrganizedConversion {
  category: DocumentCategory;
  expires_at: string;
}
export interface DocumentsPage {
  items: DocumentItem[];
  next_cursor: string | null;
}
export interface DocumentsService {
  list(query: string, signal: AbortSignal): Promise<DocumentsPage>;
}
const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
export const documentsService: DocumentsService = {
  async list(query, signal) {
    const response = await api.request(`/api/pro/documents?${query}`, {
      signal,
      cache: "no-store",
    });
    if (!response.ok)
      throw new Error(
        await readApiError(
          response,
          "Não foi possível carregar os documentos. Tente novamente.",
        ),
      );
    return response.json();
  },
};
