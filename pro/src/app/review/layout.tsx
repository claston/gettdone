import { ProSessionGate } from "@/components/pro-session-gate";

export default function ReviewLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <ProSessionGate>{children}</ProSessionGate>;
}
