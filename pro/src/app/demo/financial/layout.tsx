import { notFound } from "next/navigation";
import { financialCenterDemoVisible } from "@/config/product-visibility";

export const dynamic = "force-dynamic";

export default function FinancialDemoLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  if (!financialCenterDemoVisible) notFound();
  return children;
}
