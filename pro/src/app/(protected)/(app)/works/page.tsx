import { StatementWorks } from "@/components/works/statement-works";
import { redirect } from "next/navigation";
import { legacyOrganizationVisible } from "@/config/product-visibility";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ client?: string; period?: string }>;
}) {
  if (!legacyOrganizationVisible) redirect("/dashboard");
  const query = await searchParams;
  return (
    <StatementWorks
      initialClient={typeof query.client === "string" ? query.client : ""}
      initialPeriod={typeof query.period === "string" ? query.period : ""}
    />
  );
}
