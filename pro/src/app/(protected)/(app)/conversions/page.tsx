import { NewConversionForm } from "@/components/new-conversion-form";
import { OrganizationPage } from "@/components/organization/organization-page";

export default function Page() {
  return (
    <>
      <NewConversionForm />
      <OrganizationPage mode="conversions" />
    </>
  );
}
