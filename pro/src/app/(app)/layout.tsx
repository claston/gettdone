import { AppShell } from "@/components/app-shell";
import { ProSessionGate } from "@/components/pro-session-gate";

export default function StandardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ProSessionGate>
      <AppShell>{children}</AppShell>
    </ProSessionGate>
  );
}
