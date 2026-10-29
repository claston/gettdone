import { StatementWorks } from "@/components/works/statement-works";
import { redirect } from "next/navigation";
import { legacyOrganizationVisible } from "@/config/product-visibility";
export default async function Page({
  params,
}: {
  params: Promise<{ workId: string }>;
}) {
  if (!legacyOrganizationVisible) redirect("/dashboard");
  const { workId } = await params;
  return <StatementWorks initialId={workId} />;
}
