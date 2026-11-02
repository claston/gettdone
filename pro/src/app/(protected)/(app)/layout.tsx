import { AppShell } from "@/components/app-shell";
import { financialCenterDemoVisible } from "@/config/product-visibility";

export default function StandardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AppShell financialDemoVisible={financialCenterDemoVisible}>
      {children}
    </AppShell>
  );
}
