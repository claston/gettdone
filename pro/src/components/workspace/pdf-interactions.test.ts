import { describe, expect, it } from "vitest";
import { dragRegion, regionTargets, targetAtPoint } from "./pdf-interactions";
import {
  sourceLocation,
  transactionAtReadingPosition,
} from "./source-location";

describe("PDF reviewer geometry", () => {
  it("finds an existing line across the page width and preserves manual priority at any zoom", () => {
    const region = { page: 2, left: 0.1, top: 0.2, width: 0.4, height: 0.02 };
    const targets = regionTargets(
      [
        { id: "source", description: "Original", sourceRegions: [region] },
        { id: "manual", description: "Association", reviewRegions: [region] },
        { id: "legacy", description: "Page only", sourcePage: 2 },
      ],
      2,
    );
    for (const scale of [1, 2]) {
      const size = { width: 500 * scale, height: 1000 * scale };
      expect(
        targetAtPoint(targets, { x: 450 * scale, y: 210 * scale }, size)
          ?.transactionId,
      ).toBe("manual");
      expect(
        targetAtPoint(targets, { x: 150 * scale, y: 210 * scale }, size)
          ?.transactionId,
      ).toBe("manual");
      expect(
        targetAtPoint(targets, { x: 450 * scale, y: 100 * scale }, size),
      ).toBeUndefined();
      expect(
        targetAtPoint([], { x: 450 * scale, y: 210 * scale }, size),
      ).toBeUndefined();
    }
  });

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
