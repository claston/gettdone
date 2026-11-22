import { ClientsPage } from "@/components/clients-page";
import { financialCenterVisible } from "@/config/product-visibility";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const month = typeof query.month === "string" ? query.month : "";
  return (
    <ClientsPage
      key={month}
      month={month}
      financialCenterVisible={financialCenterVisible}
    />
  );
}
