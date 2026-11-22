import type { DocumentCategory } from "@/data/documents-service";

export const documentCategories: Record<DocumentCategory, string> = {
  all: "Todos",
  statements: "Extratos",
  invoices: "Notas fiscais",
  receipts: "Comprovantes",
  unclassified: "Outros e a identificar",
};

export function documentsUrl(
  client = "",
  type: DocumentCategory = "all",
  batch = "",
) {
  const query = new URLSearchParams();
  if (client) query.set("client", client);
  if (type !== "all") query.set("type", type);
  if (batch) query.set("batch", batch);
  return `/documents${query.size ? `?${query}` : ""}`;
}
