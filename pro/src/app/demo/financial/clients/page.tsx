import {
  FinancialDemoClients,
  FinancialDemoShell,
} from "@/components/financial/demo-shell";
export default function Page() {
  return (
    <FinancialDemoShell>
      <FinancialDemoClients />
    </FinancialDemoShell>
  );
}
