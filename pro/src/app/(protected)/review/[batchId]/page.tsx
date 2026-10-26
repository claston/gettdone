import { WorkspaceScreen } from "@/components/workspace/workspace-screen";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ batchId: string }>;
  searchParams: Promise<{ return_to?: string | string[] }>;
}) {
  const { batchId } = await params;
  const query = await searchParams;
  return (
    <WorkspaceScreen
      batchId={batchId}
      returnTo={
        typeof query.return_to === "string" ? query.return_to : undefined
      }
    />
  );
}
