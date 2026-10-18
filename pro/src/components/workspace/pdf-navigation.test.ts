import { describe, expect, it } from "vitest";
import {
  activePageAt,
  pageLayout,
  readingAnchor,
  scrollForAnchor,
} from "./pdf-navigation";

describe("PDF page geometry", () => {
  it("recognizes a short final page even when its top cannot reach the reading line", () => {
    const pages = pageLayout(
      [
        { width: 600, height: 800 },
        { width: 600, height: 300 },
      ],
      600,
    );
    expect(activePageAt(pages, 332, 800)).toBe(2);
    expect(activePageAt(pages, 0, 1200)).toBe(1);
  });
  const pages = pageLayout(
    [
      { width: 600, height: 800 },
      { width: 800, height: 600 },
      { width: 600, height: 800 },
    ],
    600,
  );
  it("uses a fixed reading line instead of visible-area percentages", () => {
    expect(activePageAt(pages, 0, 600)).toBe(1);
    expect(activePageAt(pages, 700, 600)).toBe(2);
    expect(activePageAt(pages, 710, 600)).toBe(2);
    expect(activePageAt(pages, 1300, 600)).toBe(3);
  });
  it("keeps the page and position within it across zoom and resizing", () => {
    const anchor = readingAnchor(pages, 950);
    expect(anchor.page).toBe(2);
    const resized = pageLayout(
      [
        { width: 600, height: 800 },
        { width: 800, height: 600 },
        { width: 600, height: 800 },
      ],
      900,
    );
    expect(readingAnchor(resized, scrollForAnchor(resized, anchor))).toEqual(
      anchor,
    );
  });
});
