import { ReceivedStatementsPage } from "@/components/received-statements-page";
import {
  financialCenterDemoVisible,
  financialCenterVisible,
} from "@/config/product-visibility";

export default function Page() {
  return (
    <ReceivedStatementsPage
      financialDemoVisible={financialCenterDemoVisible}
      financialCenterVisible={financialCenterVisible}
    />
  );
}
