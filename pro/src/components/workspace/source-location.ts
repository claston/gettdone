import type { SourceRegion, Transaction } from "@/domain/workspace";
import { validSourceRegions } from "@/domain/source-regions";
import { activePageAt, type PageBox } from "./pdf-navigation";

export { validSourceRegions } from "@/domain/source-regions";

export interface PdfSourceLocation {
  page: number;
  transactionId?: string;
  regions: SourceRegion[];
}
export interface PdfNavigationRequest extends PdfSourceLocation {
  token: number;
  scroll?: boolean;
  origin?: "grid" | "pdf" | "pdf-selection";
}

export type SourceSelectionOptions = Pick<
  PdfNavigationRequest,
  "scroll" | "origin"
>;
export interface PdfReadingPosition {
  page: number;
  fraction: number;
}

export function pdfReadingPosition(
  pages: PageBox[],
  top: number,
  height: number,
): PdfReadingPosition {
  const page = activePageAt(pages, top, height);
  const box = pages[page - 1];
  const atEnd =
    page === pages.length &&
    top > 0 &&
    top + height >= box.top + box.height - 1;
  return {
    page,
    fraction: box
      ? atEnd
        ? 1
        : Math.max(
            0,
            Math.min(
              1,
              (top + Math.min(120, height * 0.2) - box.top) / box.height,
            ),
          )
      : 0,
  };
}

export function transactionAtReadingPosition<
  T extends Pick<
    Transaction,
    "id" | "sourcePage" | "sourceRegions" | "reviewRegions"
  >,
>(rows: readonly T[], position: PdfReadingPosition): T | undefined {
  let selected: T | undefined;
  let distance = Infinity;
  for (const row of rows) {
    for (const region of transactionRegions(row)) {
      if (region.page !== position.page) continue;
      const candidate = Math.abs(
        region.top + region.height / 2 - position.fraction,
      );
      if (candidate < distance) {
        selected = row;
        distance = candidate;
      }
    }
  }
  return selected ?? rows.find((row) => row.sourcePage === position.page);
}

export function sourceLocation(
  transaction: Pick<
    Transaction,
    "id" | "sourcePage" | "sourceRegions" | "reviewRegions"
  >,
): PdfSourceLocation | null {
  const regions = transactionRegions(transaction);
  const page = regions[0]?.page ?? transaction.sourcePage;
  if (!page || !Number.isInteger(page) || page < 1) return null;
  return { page, transactionId: transaction.id, regions };
}

export function transactionRegions(
  transaction: Pick<Transaction, "sourceRegions" | "reviewRegions">,
) {
  return validSourceRegions([
    ...(transaction.reviewRegions ?? []),
    ...(transaction.sourceRegions ?? []),
  ]).filter(
    (region, index, all) =>
      all.findIndex(
        (item) => JSON.stringify(item) === JSON.stringify(region),
      ) === index,
  );
}

export function regionScrollPosition(
  box: PageBox,
  region: SourceRegion,
  viewportHeight: number,
  alignReadingLine = false,
): number {
  return Math.max(
    box.top,
    box.top +
      (region.top + region.height / 2) * box.height -
      (alignReadingLine
        ? Math.min(120, viewportHeight * 0.2)
        : viewportHeight * 0.35),
  );
}
