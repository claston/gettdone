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
  month = "",
  account = "",
  view = "",
) {
  const query = new URLSearchParams();
  if (client) query.set("client", client);
  if (type !== "all") query.set("type", type);
  if (batch) query.set("batch", batch);
  if (month) query.set("month", month);
  if (account) query.set("account", account);
  if (view) query.set("view", view);
  return `/documents${query.size ? `?${query}` : ""}`;
}
