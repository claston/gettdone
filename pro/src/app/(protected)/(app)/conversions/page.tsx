import { ConversionsPage } from "@/components/conversions-page";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const value = (key: string) =>
    typeof query[key] === "string" ? (query[key] as string) : "";
  return (
    <ConversionsPage
      key={value("view")}
      initialView={value("view") === "files" ? "files" : "batches"}
      initialFilters={{
        client: value("client"),
        account: value("account"),
        period: value("period"),
      }}
      initialCursor={value("cursor") || undefined}
      workId={
        /^work_[A-Za-z0-9]+$/.test(value("work")) ? value("work") : undefined
      }
    />
  );
}
