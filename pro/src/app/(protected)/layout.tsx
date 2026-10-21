import { ProSessionGate } from "@/components/pro-session-gate";
import { SessionPrefetch } from "@/components/session-prefetch";

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ProSessionGate>
      <SessionPrefetch />
      {children}
    </ProSessionGate>
  );
}
