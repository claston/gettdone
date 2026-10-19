import type { SourceRegion, Transaction } from "@/domain/workspace";
import { validSourceRegions } from "@/domain/source-regions";

type EvidenceRow = Pick<
  Transaction,
  | "id"
  | "description"
  | "sourcePage"
  | "sourceRegions"
  | "reviewRegions"
  | "isDeleted"
>;
export interface PdfRegionTarget {
  transactionId: string;
  description: string;
  region: SourceRegion;
  kind: "manual" | "extracted";
}
export interface PdfReviewControls {
  transactions: Transaction[];
  selectedId?: string;
  drawingFor: { id: string; description: string } | null;
  paused: boolean;
  onSelect(id: string, page: number): void;
  onStartDrawing(): void;
  onCancelDrawing(): void;
  onCreateRegion(region: SourceRegion): void;
  onClearRegions(): void;
}

export function regionTargets(
  rows: readonly EvidenceRow[],
  page: number,
): PdfRegionTarget[] {
  return (
    rows
      .filter((row) => !row.isDeleted)
      .flatMap((row) =>
        (["extracted", "manual"] as const).flatMap((kind) =>
          validSourceRegions(
            kind === "manual" ? row.reviewRegions : row.sourceRegions,
          )
            .filter((region) => region.page === page)
            .map((region) => ({
              transactionId: row.id,
              description: row.description,
              region,
              kind,
            })),
        ),
      )
      // Manual associations take priority over automatic targets. Within each
      // kind, small boxes remain reachable above broad OCR evidence.
      .sort(
        (a, b) =>
          (a.kind === b.kind ? 0 : a.kind === "manual" ? 1 : -1) ||
          b.region.width * b.region.height - a.region.width * a.region.height,
      )
  );
}

export function dragRegion(
  page: number,
  start: { x: number; y: number },
  end: { x: number; y: number },
  size: { width: number; height: number },
): SourceRegion | null {
  if (
    ![start.x, start.y, end.x, end.y, size.width, size.height].every(
      Number.isFinite,
    ) ||
    size.width <= 0 ||
    size.height <= 0
  )
    return null;
  const x = (value: number) => Math.max(0, Math.min(size.width, value));
  const y = (value: number) => Math.max(0, Math.min(size.height, value));
  const left = Math.min(x(start.x), x(end.x)),
    top = Math.min(y(start.y), y(end.y));
  const width = Math.abs(x(end.x) - x(start.x)),
    height = Math.abs(y(end.y) - y(start.y));
  if (width < 6 || height < 3) return null;
  return (
    validSourceRegions([
      {
        page,
        left: left / size.width,
        top: top / size.height,
        width: width / size.width,
        height: height / size.height,
      },
    ])[0] ?? null
  );
}
