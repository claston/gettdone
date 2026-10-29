import { OrganizationPage } from "@/components/organization/organization-page";
import { redirect } from "next/navigation";
import { legacyOrganizationVisible } from "@/config/product-visibility";

export default function Page() {
  if (!legacyOrganizationVisible) redirect("/dashboard");
  return (
    <main>
      <OrganizationPage mode="periods" />
    </main>
  );
}
