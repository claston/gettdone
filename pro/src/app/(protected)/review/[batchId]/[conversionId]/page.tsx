import { WorkspaceScreen } from "@/components/workspace/workspace-screen";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ batchId: string; conversionId: string }>;
  searchParams: Promise<{ return_to?: string | string[] }>;
}) {
  const { batchId, conversionId } = await params;
  const query = await searchParams;
  return (
    <WorkspaceScreen
      batchId={batchId}
      initialConversionId={conversionId}
      returnTo={
        typeof query.return_to === "string" ? query.return_to : undefined
      }
    />
  );
}
