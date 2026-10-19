import { describe, expect, it } from "vitest";
import {
  sourceLocation,
  validSourceRegions,
  regionScrollPosition,
  transactionAtReadingPosition,
  pdfReadingPosition,
} from "./source-location";

const region = { page: 2, left: 0.1, top: 0.6, width: 0.6, height: 0.03 };
describe("transaction source locations", () => {
  it("follows real regions within a page, including continuations, without matching editable text", () => {
    const rows = [
      {
        id: "a",
        sourcePage: 1,
        sourceRegions: [{ ...region, page: 1, top: 0.1 }],
      },
      {
        id: "b",
        sourcePage: 1,
        sourceRegions: [
          { ...region, page: 1, top: 0.7 },
          { ...region, page: 2, top: 0.1 },
        ],
      },
      { id: "legacy", sourcePage: 3 },
      { id: "manual" },
    ];
    expect(
      transactionAtReadingPosition(rows, { page: 1, fraction: 0.12 })?.id,
    ).toBe("a");
    expect(
      transactionAtReadingPosition(rows, { page: 1, fraction: 0.8 })?.id,
    ).toBe("b");
    expect(
      transactionAtReadingPosition(rows, { page: 2, fraction: 0.1 })?.id,
    ).toBe("b");
    expect(
      transactionAtReadingPosition(rows, { page: 3, fraction: 0.8 })?.id,
    ).toBe("legacy");
    expect(
      transactionAtReadingPosition(rows, { page: 4, fraction: 0.2 }),
    ).toBeUndefined();
    expect(
      transactionAtReadingPosition([rows[0]], { page: 1, fraction: 0.8 })?.id,
    ).toBe("a");
  });
  it("uses the same reading position at different zooms and reaches the final region at the bottom", () => {
    expect(
      pdfReadingPosition([{ top: 0, width: 600, height: 1000 }], 400, 500),
    ).toEqual({ page: 1, fraction: 0.5 });
    expect(
      pdfReadingPosition([{ top: 0, width: 1200, height: 2000 }], 900, 500),
    ).toEqual({ page: 1, fraction: 0.5 });
    expect(
      pdfReadingPosition([{ top: 0, width: 600, height: 1000 }], 500, 500),
    ).toEqual({ page: 1, fraction: 1 });
  });
  it("aligns table-driven navigation with the PDF reading line to keep direction changes stable", () => {
    const box = { top: 0, width: 600, height: 2000 };
    const target = { ...region, page: 1 };
    const top = regionScrollPosition(box, target, 500, true);
    expect(pdfReadingPosition([box], top, 500).fraction).toBeCloseTo(
      target.top + target.height / 2,
    );
  });
  it("uses immutable source regions before the page fallback", () => {
    expect(
      sourceLocation({ id: "stable", sourcePage: 1, sourceRegions: [region] }),
    ).toEqual({ page: 2, transactionId: "stable", regions: [region] });
    expect(sourceLocation({ id: "old", sourcePage: 3 })).toEqual({
      page: 3,
      transactionId: "old",
      regions: [],
    });
    expect(sourceLocation({ id: "manual" })).toBeNull();
  });
  it("rejects malformed, out-of-bounds and nonfinite geometry", () => {
    expect(
      validSourceRegions([
        region,
        { ...region, page: 0 },
        { ...region, top: NaN },
        { ...region, left: 0.8 },
        null,
      ]),
    ).toEqual([region]);
    expect(validSourceRegions([region], 1)).toEqual([]);
  });
  it("navigates inside a page and transforms normalized coordinates at any scale", () => {
    expect(
      regionScrollPosition({ top: 1000, width: 600, height: 800 }, region, 500),
    ).toBeCloseTo(1317);
    expect(
      regionScrollPosition(
        { top: 2000, width: 1200, height: 1600 },
        region,
        500,
      ),
    ).toBeCloseTo(2809);
  });
});
