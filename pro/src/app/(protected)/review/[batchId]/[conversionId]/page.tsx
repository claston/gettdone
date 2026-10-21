import { WorkspaceScreen } from "@/components/workspace/workspace-screen";

export default async function Page({
  params,
}: {
  params: Promise<{ batchId: string; conversionId: string }>;
}) {
  const { batchId, conversionId } = await params;
  return (
    <WorkspaceScreen batchId={batchId} initialConversionId={conversionId} />
  );
}
