import { ClientsPage } from "@/components/clients-page";
import { financialCenterVisible } from "@/config/product-visibility";

export default function Page() {
  return <ClientsPage financialCenterVisible={financialCenterVisible} />;
}
