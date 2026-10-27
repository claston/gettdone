import { StatementWorks } from "@/components/works/statement-works";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ client?: string; period?: string }>;
}) {
  const query = await searchParams;
  return (
    <StatementWorks
      initialClient={typeof query.client === "string" ? query.client : ""}
      initialPeriod={typeof query.period === "string" ? query.period : ""}
    />
  );
}
