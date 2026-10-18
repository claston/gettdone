import type { SourceRegion } from "./workspace";

export function validSourceRegions(
  value: unknown,
  pageCount = Infinity,
): SourceRegion[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is SourceRegion => {
      if (!item || typeof item !== "object") return false;
      const { page, left, top, width, height } = item;
      return (
        Number.isInteger(page) &&
        page >= 1 &&
        page <= pageCount &&
        [left, top, width, height].every(
          (v) => typeof v === "number" && Number.isFinite(v),
        ) &&
        left >= 0 &&
        top >= 0 &&
        width > 0 &&
        height > 0 &&
        left + width <= 1.000001 &&
        top + height <= 1.000001
      );
    })
    .map(({ page, left, top, width, height }) => ({
      page,
      left,
      top,
      width,
      height,
    }));
}
