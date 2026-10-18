import { describe, expect, it } from "vitest";
import {
  sourceLocation,
  validSourceRegions,
  regionScrollPosition,
} from "./source-location";

const region = { page: 2, left: 0.1, top: 0.6, width: 0.6, height: 0.03 };
describe("transaction source locations", () => {
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
