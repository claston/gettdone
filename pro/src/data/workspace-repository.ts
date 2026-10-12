import { createHttpWorkspaceRepository } from "./http-workspace-repository";
import { mockWorkspaceRepository } from "./mock-workspace-repository";

const apiUrl = process.env.NEXT_PUBLIC_API_URL?.trim();

export const workspaceRepository = apiUrl
  ? createHttpWorkspaceRepository({ apiUrl })
  : mockWorkspaceRepository;
