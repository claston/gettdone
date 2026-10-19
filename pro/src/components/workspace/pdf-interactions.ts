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

/** Read across a known line even when the pointer is beside its glyphs.
 * Empty headers/footers and page-only provenance never acquire an invented row.
 */
export function targetAtPoint(
  targets: readonly PdfRegionTarget[],
  point: { x: number; y: number },
  size: { width: number; height: number },
): PdfRegionTarget | undefined {
  if (
    ![point.x, point.y, size.width, size.height].every(Number.isFinite) ||
    size.width <= 0 ||
    size.height <= 0 ||
    point.x < 0 ||
    point.y < 0 ||
    point.x > size.width ||
    point.y > size.height
  )
    return;
  let selected: PdfRegionTarget | undefined;
  let vertical = Infinity;
  let horizontal = Infinity;
  // Targets use DOM paint order; explicit manual associations win overlaps.
  for (let index = targets.length - 1; index >= 0; index--) {
    const target = targets[index];
    const region = target.region;
    const top = region.top * size.height;
    const bottom = (region.top + region.height) * size.height;
    if (point.y < top - 6 || point.y > bottom + 6) continue;
    const left = region.left * size.width;
    const right = (region.left + region.width) * size.width;
    const dx = Math.max(left - point.x, 0, point.x - right);
    if (!dx && point.y >= top && point.y <= bottom) return target;
    const dy = Math.abs(point.y - (top + bottom) / 2);
    if (dy < vertical || (dy === vertical && dx < horizontal)) {
      selected = target;
      vertical = dy;
      horizontal = dx;
    }
  }
  return selected;
}
