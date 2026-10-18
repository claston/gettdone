import type { SourceRegion, Transaction } from "@/domain/workspace";
import { validSourceRegions } from "@/domain/source-regions";
import type { PageBox } from "./pdf-navigation";

export { validSourceRegions } from "@/domain/source-regions";

export interface PdfSourceLocation {
  page: number;
  transactionId?: string;
  regions: SourceRegion[];
}
export interface PdfNavigationRequest extends PdfSourceLocation {
  token: number;
}

export function sourceLocation(
  transaction: Pick<Transaction, "id" | "sourcePage" | "sourceRegions">,
): PdfSourceLocation | null {
  const regions = validSourceRegions(transaction.sourceRegions);
  const page = regions[0]?.page ?? transaction.sourcePage;
  if (!page || !Number.isInteger(page) || page < 1) return null;
  return { page, transactionId: transaction.id, regions };
}

export function regionScrollPosition(
  box: PageBox,
  region: SourceRegion,
  viewportHeight: number,
): number {
  return Math.max(
    box.top,
    box.top +
      (region.top + region.height / 2) * box.height -
      viewportHeight * 0.35,
  );
}
