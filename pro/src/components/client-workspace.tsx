"use client";

import { useMemo } from "react";
import { createHttpWorkspaceRepository } from "@/data/http-workspace-repository";
import { WorkspaceScreen } from "./workspace/workspace-screen";

export function ClientWorkspace({
  clientId,
  conversionId,
}: {
  clientId: string;
  conversionId?: string;
}) {
  const repository = useMemo(
    () =>
      createHttpWorkspaceRepository({
        apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
        clientId,
      }),
    [clientId],
  );
  return (
    <WorkspaceScreen
      batchId={clientId}
      initialConversionId={conversionId}
      repository={repository}
      clientMode
      returnTo="/dashboard"
    />
  );
}
