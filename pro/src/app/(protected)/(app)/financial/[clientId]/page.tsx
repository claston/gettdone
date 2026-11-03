import { notFound } from "next/navigation";
import { LiveFinancialCenter } from "@/components/financial/live-financial-center";
import { financialCenterVisible } from "@/config/product-visibility";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ clientId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  if (!financialCenterVisible) notFound();
  const { clientId } = await params;
  const values = await searchParams;
  const query = new URLSearchParams();
  for (const key of ["month", "accounts", "day", "tab", "pending"]) {
    if (typeof values[key] === "string") query.set(key, values[key]);
  }
  return (
    <LiveFinancialCenter
      key={clientId}
      clientId={clientId}
      initialQuery={query.toString()}
    />
  );
}
