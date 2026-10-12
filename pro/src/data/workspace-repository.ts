import { createHttpWorkspaceRepository } from "./http-workspace-repository";
import { mockWorkspaceRepository } from "./mock-workspace-repository";
import type { WorkspaceRepository } from "@/domain/workspace";

export type WorkspaceDataMode = "api" | "demo";

const apiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();

export const workspaceRepository = apiUrl
  ? createHttpWorkspaceRepository({ apiUrl })
  : mockWorkspaceRepository;

export function resolveWorkspaceRepository(
  mode: WorkspaceDataMode,
  configuredRepository: WorkspaceRepository = workspaceRepository,
): WorkspaceRepository {
  return mode === "demo" ? mockWorkspaceRepository : configuredRepository;
}
