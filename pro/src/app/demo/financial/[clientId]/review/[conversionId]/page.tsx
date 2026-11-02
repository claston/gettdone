import { notFound } from "next/navigation";
import { getFinancialScenario } from "@/data/financial-demo";
import { FinancialDemoReview } from "@/components/financial/financial-demo-review";
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ clientId: string; conversionId: string }>;
  searchParams: Promise<{ returnTo?: string }>;
}) {
  const { clientId, conversionId } = await params;
  const scenario = getFinancialScenario(clientId);
  if (
    !scenario ||
    !scenario.sources.some(
      (source) =>
        source.conversionId === conversionId &&
        source.availability === "available",
    )
  )
    notFound();
  const { returnTo } = await searchParams;
  return (
    <FinancialDemoReview
      scenario={scenario}
      conversionId={conversionId}
      returnTo={returnTo ?? `/demo/financial/${clientId}`}
    />
  );
}
