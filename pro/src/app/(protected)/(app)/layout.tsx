import { AppShell } from "@/components/app-shell";

export default function StandardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
