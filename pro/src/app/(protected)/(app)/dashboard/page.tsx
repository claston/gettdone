import { ReceivedStatementsPage } from "@/components/received-statements-page";
import { financialCenterDemoVisible } from "@/config/product-visibility";

export default function Page() {
  return (
    <ReceivedStatementsPage financialDemoVisible={financialCenterDemoVisible} />
  );
}
