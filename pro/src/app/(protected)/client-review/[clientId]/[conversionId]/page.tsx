import { ClientWorkspace } from "@/components/client-workspace";

export default async function Page({
  params,
}: {
  params: Promise<{ clientId: string; conversionId: string }>;
}) {
  const { clientId, conversionId } = await params;
  return <ClientWorkspace clientId={clientId} conversionId={conversionId} />;
}
