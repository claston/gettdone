import { describe, expect, it } from "vitest";
import { dragRegion, regionTargets } from "./pdf-interactions";
import {
  sourceLocation,
  transactionAtReadingPosition,
} from "./source-location";

describe("PDF reviewer geometry", () => {
  it("uses a manual association without replacing the original extraction evidence", () => {
    const original = { page: 1, left: 0.1, top: 0.2, width: 0.5, height: 0.03 };
    const manual = { ...original, page: 2 };
    const row = {
      id: "stable",
      sourceRegions: [original],
      reviewRegions: [manual],
    };
    expect(sourceLocation(row)).toEqual({
      page: 2,
      transactionId: "stable",
      regions: [manual, original],
    });
    expect(
      transactionAtReadingPosition([row], { page: 2, fraction: 0.21 })?.id,
    ).toBe("stable");
    expect(row.sourceRegions).toEqual([original]);
  });
  it("normalizes a reverse drag, clips to the visible page and rejects tiny/cancelled gestures", () => {
    const page = { width: 500, height: 1000 };
    expect(dragRegion(2, { x: 450, y: 400 }, { x: -20, y: 300 }, page)).toEqual(
      { page: 2, left: 0, top: 0.3, width: 0.9, height: 0.1 },
    );
    expect(dragRegion(1, { x: 20, y: 30 }, { x: 22, y: 31 }, page)).toBeNull();
  });
  it("uses actual automatic and reviewer boxes, preserving IDs across pages and excluding deleted rows", () => {
    const original = { page: 1, left: 0.1, top: 0.2, width: 0.5, height: 0.03 };
    const manual = { ...original, page: 2 };
    const rows = [
      {
        id: "stable",
        description: "EDITED",
        sourceRegions: [original],
        reviewRegions: [manual],
      },
      {
        id: "deleted",
        description: "Removed",
        sourceRegions: [original],
        isDeleted: true,
      },
    ];
    expect(
      regionTargets(rows, 2).map((target) => [
        target.transactionId,
        target.kind,
        target.region.page,
      ]),
    ).toEqual([["stable", "manual", 2]]);
    expect(regionTargets(rows, 1)[0].kind).toBe("extracted");
    expect(
      regionTargets(
        [{ id: "old", description: "No coordinates", sourcePage: 1 }],
        1,
      ),
    ).toEqual([]);
  });
});
