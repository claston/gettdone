import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PdfSourceHighlights } from "./pdf-source-highlights";

describe("PDF original evidence overlays", () => {
  it("anchors multiple rectangles to source geometry, independently of editable content", () => {
    const regions = [
      { page: 2, left: 0.1, top: 0.2, width: 0.6, height: 0.02 },
      { page: 2, left: 0.1, top: 0.23, width: 0.4, height: 0.02 },
    ];
    const { rerender } = render(
      <PdfSourceHighlights transactionId="stable" regions={regions} />,
    );
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(screen.getAllByRole("img")[0]).toHaveStyle({
      left: "10%",
      top: "20%",
      width: "60%",
      height: "2%",
    });
    rerender(<PdfSourceHighlights transactionId="different" regions={[]} />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });
});
