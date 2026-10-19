import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Transaction } from "@/domain/workspace";
import type { PdfReviewControls } from "./pdf-interactions";
import { PdfInteractionLayer } from "./pdf-interaction-layer";

const region = { page: 2, left: 0.1, top: 0.2, width: 0.6, height: 0.03 };
const transaction: Transaction = {
  id: "stable",
  description: "PIX original",
  date: "01/04/2026",
  credit: 10,
  debit: null,
  balance: 110,
  sourceRegions: [region],
  fieldStates: {
    date: "ORIGINAL",
    description: "ORIGINAL",
    credit: "ORIGINAL",
    debit: "ORIGINAL",
    balance: "ORIGINAL",
  },
};
const controls = (): PdfReviewControls => ({
  transactions: [transaction],
  drawingFor: null,
  paused: false,
  onSelect: vi.fn(),
  onStartDrawing: vi.fn(),
  onCancelDrawing: vi.fn(),
  onCreateRegion: vi.fn(),
  onClearRegions: vi.fn(),
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("interactive PDF evidence", () => {
  it("does not save a draft if its page scrolls during the gesture", () => {
    class TestPointerEvent extends MouseEvent {
      pointerId = 1;
    }
    vi.stubGlobal("PointerEvent", TestPointerEvent);
    const review = {
      ...controls(),
      drawingFor: { id: "stable", description: "PIX original" },
    };
    const { container } = render(
      <div className="pdf-stage">
        <PdfInteractionLayer page={2} review={review} />
      </div>,
    );
    const stage = container.firstElementChild as HTMLElement;
    const layer = stage.firstElementChild as HTMLElement;
    vi.spyOn(layer, "getBoundingClientRect").mockReturnValue(
      new DOMRect(0, 0, 500, 1000),
    );
    Object.assign(layer, {
      setPointerCapture: vi.fn(),
      hasPointerCapture: () => true,
      releasePointerCapture: vi.fn(),
    });
    fireEvent.pointerDown(layer, { button: 0, clientX: 50, clientY: 100 });
    stage.scrollTop = 30;
    fireEvent.scroll(stage);
    fireEvent.pointerUp(layer, { clientX: 300, clientY: 130 });
    expect(review.onCreateRegion).not.toHaveBeenCalled();
  });
  it("selects the exact stable transaction by click and drops pending hover during editing", () => {
    vi.useFakeTimers();
    const review = controls();
    const { rerender } = render(
      <PdfInteractionLayer page={2} review={review} />,
    );
    const button = screen.getByRole("button", {
      name: "Selecionar PIX original no revisor (página 2)",
    });
    fireEvent.click(button);
    expect(review.onSelect).toHaveBeenCalledWith("stable", 2);
    vi.mocked(review.onSelect).mockClear();
    fireEvent.pointerMove(button, { clientX: 20, clientY: 30 });
    rerender(
      <PdfInteractionLayer page={2} review={{ ...review, paused: true }} />,
    );
    act(() => vi.advanceTimersByTime(150));
    expect(review.onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it("draws an associated clipped rectangle and cancels a subsequent draft with Escape", () => {
    class TestPointerEvent extends MouseEvent {
      pointerId = 1;
    }
    vi.stubGlobal("PointerEvent", TestPointerEvent);
    const review = {
      ...controls(),
      drawingFor: { id: "stable", description: "PIX original" },
    };
    const { container } = render(
      <PdfInteractionLayer page={2} review={review} />,
    );
    const layer = container.firstElementChild as HTMLElement;
    vi.spyOn(layer, "getBoundingClientRect").mockReturnValue(
      new DOMRect(100, 200, 500, 1000),
    );
    Object.assign(layer, {
      setPointerCapture: vi.fn(),
      hasPointerCapture: () => true,
      releasePointerCapture: vi.fn(),
    });
    fireEvent.pointerDown(layer, { button: 0, clientX: 550, clientY: 600 });
    fireEvent.pointerMove(layer, { clientX: 80, clientY: 500 });
    fireEvent.pointerUp(layer, { clientX: 80, clientY: 500 });
    expect(review.onCreateRegion).toHaveBeenCalledExactlyOnceWith({
      page: 2,
      left: 0,
      top: 0.3,
      width: 0.9,
      height: 0.1,
    });
    fireEvent.pointerDown(layer, { button: 0, clientX: 200, clientY: 300 });
    fireEvent.keyDown(layer, { key: "Escape" });
    fireEvent.pointerUp(layer, { clientX: 400, clientY: 350 });
    expect(review.onCancelDrawing).toHaveBeenCalledTimes(1);
    expect(review.onCreateRegion).toHaveBeenCalledTimes(1);
    expect(review.onSelect).not.toHaveBeenCalled();
  });
});
