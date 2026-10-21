import { WorkspaceScreen } from "@/components/workspace/workspace-screen";

export default async function Page({
  params,
}: {
  params: Promise<{ batchId: string }>;
}) {
  const { batchId } = await params;
  return <WorkspaceScreen batchId={batchId} />;
}
