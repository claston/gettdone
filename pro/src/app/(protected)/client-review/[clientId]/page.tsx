import { ClientWorkspace } from "@/components/client-workspace";

export default async function Page({
  params,
}: {
  params: Promise<{ clientId: string }>;
}) {
  const { clientId } = await params;
  return <ClientWorkspace clientId={clientId} />;
}
