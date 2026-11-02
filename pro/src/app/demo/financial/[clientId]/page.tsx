import { notFound } from "next/navigation";
import { FinancialDemoShell } from "@/components/financial/demo-shell";
import { FinancialCenter } from "@/components/financial/financial-center";
import { getFinancialScenario } from "@/data/financial-demo";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ clientId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { clientId } = await params;
  const scenario = getFinancialScenario(clientId);
  if (!scenario) notFound();
  const search = await searchParams;
  const query = new URLSearchParams();
  for (const key of ["month", "accounts", "tab", "day"])
    if (typeof search[key] === "string") query.set(key, search[key]);
  return (
    <FinancialDemoShell>
      <FinancialCenter
        key={clientId}
        scenario={scenario}
        initialQuery={query.toString()}
      />
    </FinancialDemoShell>
  );
}
