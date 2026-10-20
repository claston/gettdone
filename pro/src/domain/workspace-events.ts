export const workspaceEvents = [
  "workspace_opened",
  "conversion_selected",
  "suspicious_filter_used",
  "transaction_edited",
  "conversion_reviewed",
  "batch_export_requested",
] as const;
export type WorkspaceEvent = (typeof workspaceEvents)[number];

export function emitWorkspaceEvent(
  repository: { recordEvent?(event: WorkspaceEvent): void },
  event: WorkspaceEvent,
) {
  try {
    repository.recordEvent?.(event);
  } catch {
    /* Telemetry must not interrupt review. */
  }
}
