"use client";
import { financialDemoRepository } from "@/data/financial-demo";
import type { FinancialScenario } from "@/domain/financial-center";
import { WorkspaceScreen } from "@/components/workspace/workspace-screen";
export function FinancialDemoReview({
  scenario,
  conversionId,
  returnTo,
}: {
  scenario: FinancialScenario;
  conversionId: string;
  returnTo: string;
}) {
  return (
    <WorkspaceScreen
      batchId={scenario.batch.id}
      initialConversionId={conversionId}
      repository={financialDemoRepository(scenario)}
      dataMode="demo"
      returnTo={returnTo}
      conversionHref={(id) =>
        `/demo/financial/${scenario.id}/review/${id}?returnTo=${encodeURIComponent(returnTo)}`
      }
    />
  );
}
