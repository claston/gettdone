import { DocumentsPage } from "@/components/documents/documents-page";
import { documentCategories } from "@/domain/documents-navigation";
import type { DocumentCategory } from "@/data/documents-service";
import { financialCenterVisible } from "@/config/product-visibility";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const value = (key: string) =>
    typeof query[key] === "string" ? (query[key] as string) : "";
  const client = value("client");
  const category = Object.hasOwn(documentCategories, value("type"))
    ? (value("type") as DocumentCategory)
    : "all";
  const batch = value("batch");
  return (
    <DocumentsPage
      key={`${client}:${category}:${batch}`}
      clientId={client}
      category={category}
      batchId={batch}
      financialCenterVisible={financialCenterVisible}
    />
  );
}
