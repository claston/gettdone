import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PdfSourceHighlights } from "./pdf-source-highlights";

describe("PDF original evidence overlays", () => {
  it("shows an automatic full-width reading marker for the selected row and follows its real geometry", () => {
    const first = { page: 2, left: 0.1, top: 0.2, width: 0.3, height: 0.02 };
    const { container, rerender } = render(
      <PdfSourceHighlights
        transactionId="first"
        regions={[first, { ...first, left: 0.6 }]}
      />,
    );
    const marker = container.querySelector("[data-pdf-reading-marker]")!;
    expect(
      container.querySelectorAll("[data-pdf-reading-marker]"),
    ).toHaveLength(1);
    expect(marker).toHaveAttribute("data-source-transaction", "first");
    expect(marker).toHaveStyle({
      left: "0%",
      width: "100%",
      top: "20%",
      height: "2%",
    });
    rerender(
      <PdfSourceHighlights
        transactionId="next"
        regions={[{ ...first, top: 0.6 }]}
      />,
    );
    expect(
      container.querySelector("[data-pdf-reading-marker]"),
    ).toHaveAttribute("data-source-transaction", "next");
    expect(container.querySelector("[data-pdf-reading-marker]")).toHaveStyle({
      top: "60%",
    });
    expect(first.top).toBe(0.2);
    rerender(<PdfSourceHighlights transactionId="legacy" regions={[]} />);
    expect(container.querySelector("[data-pdf-reading-marker]")).toBeNull();
  });

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
