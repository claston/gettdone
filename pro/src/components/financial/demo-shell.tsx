"use client";
import { AppShell } from "@/components/app-shell";
import { ReceivedStatementsPage } from "@/components/received-statements-page";
import { financialDemoClients } from "@/data/financial-demo";
export function FinancialDemoShell({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell demo financialDemoVisible>
      {children}
    </AppShell>
  );
}
export function FinancialDemoClients() {
  return (
    <ReceivedStatementsPage service={financialDemoClients} financialDemo />
  );
}
