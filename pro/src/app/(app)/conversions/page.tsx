import { NewConversionForm } from "@/components/new-conversion-form";
import { ProSessionGate } from "@/components/pro-session-gate";

export default function Page() {
  return (
    <ProSessionGate>
      <NewConversionForm />
    </ProSessionGate>
  );
}
