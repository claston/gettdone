"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";

const clamp = (value: number) => Math.max(0.5, Math.min(2, value));
const editable = (element: EventTarget | null) =>
  element instanceof Element &&
  Boolean(
    element.closest(
      "input, textarea, select, [contenteditable]:not([contenteditable='false'])",
    ),
  );

export function usePdfZoom(
  stage: RefObject<HTMLElement | null>,
  enabled: boolean,
) {
  const [zoom, setZoom] = useState(1);
  const increase = useCallback(
    () => setZoom((value) => clamp(Math.round((value + 0.1) * 1000) / 1000)),
    [],
  );
  const decrease = useCallback(
    () => setZoom((value) => clamp(Math.round((value - 0.1) * 1000) / 1000)),
    [],
  );
  const reset = useCallback(() => setZoom(1), []);

  useEffect(() => {
    const element = stage.current;
    if (!element || !enabled) return;
    const panel = element.closest<HTMLElement>(".pdf-panel") ?? element;
    let hovering = false;
    const enter = () => {
      hovering = true;
    };
    const leave = () => {
      hovering = false;
    };
    const wheel = (event: WheelEvent) => {
      if (
        !(event.ctrlKey || event.metaKey) ||
        event.altKey ||
        !event.deltaY ||
        !event.cancelable
      )
        return;
      event.preventDefault();
      // Trackpad pinch uses Ctrl+wheel with very small pixel deltas. Normalize
      // line/page wheels and keep the gesture smooth without changing scrolling.
      const unit =
        event.deltaMode === 1
          ? 16
          : event.deltaMode === 2
            ? element.clientHeight
            : 1;
      const delta = Math.max(-100, Math.min(100, event.deltaY * unit));
      setZoom((value) => clamp(value * Math.exp(-delta / 1000)));
    };
    const keydown = (event: KeyboardEvent) => {
      const focused = panel.contains(document.activeElement);
      const modified = event.ctrlKey || event.metaKey;
      if (
        event.defaultPrevented ||
        event.altKey ||
        event.isComposing ||
        editable(event.target) ||
        editable(document.activeElement) ||
        (!focused && !(modified && hovering))
      )
        return;
      const action = ["+", "="].includes(event.key)
        ? increase
        : ["-", "_"].includes(event.key)
          ? decrease
          : event.key === "0"
            ? reset
            : null;
      if (!action) return;
      event.preventDefault();
      action();
    };
    panel.addEventListener("mouseenter", enter);
    panel.addEventListener("mouseleave", leave);
    // A native, non-passive listener is required to cancel browser zoom.
    panel.addEventListener("wheel", wheel, { passive: false });
    window.addEventListener("keydown", keydown);
    return () => {
      panel.removeEventListener("mouseenter", enter);
      panel.removeEventListener("mouseleave", leave);
      panel.removeEventListener("wheel", wheel);
      window.removeEventListener("keydown", keydown);
    };
  }, [stage, enabled, increase, decrease, reset]);

  return { zoom, increase, decrease, reset };
}
