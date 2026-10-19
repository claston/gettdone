"use client";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent } from "react";
import type { SourceRegion } from "@/domain/workspace";
import {
  dragRegion,
  regionTargets,
  targetAtPoint,
  type PdfReviewControls,
} from "./pdf-interactions";

export function PdfInteractionLayer({
  page,
  review,
  onGestureStart,
}: {
  page: number;
  review: PdfReviewControls;
  onGestureStart?(): void;
}) {
  const [draft, setDraft] = useState<SourceRegion | null>(null);
  const start = useRef<{ x: number; y: number; pointerId: number } | null>(
    null,
  );
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingTarget = useRef<string | null>(null);
  const lastPointer = useRef<{ x: number; y: number } | null>(null);
  const latest = useRef(review);
  const layer = useRef<HTMLDivElement>(null);
  const scrollOrigin = useRef<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    latest.current = review;
  }, [review]);
  const targets = useMemo(
    () => regionTargets(review.transactions, page),
    [review.transactions, page],
  );
  const cancelHover = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    pendingTarget.current = null;
  };
  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );
  useEffect(() => {
    if (!layer.current || typeof ResizeObserver === "undefined") return;
    let previous = layer.current.getBoundingClientRect();
    const observer = new ResizeObserver(() => {
      const current = layer.current?.getBoundingClientRect();
      if (!current) return;
      if (
        current.width !== previous.width ||
        current.height !== previous.height
      ) {
        start.current = null;
        setDraft(null);
      }
      previous = current;
    });
    observer.observe(layer.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const stage = layer.current?.closest<HTMLElement>(".pdf-stage");
    if (!stage) return;
    const cancelMovedDraft = () => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
      pendingTarget.current = null;
      const origin = scrollOrigin.current;
      if (
        start.current &&
        origin &&
        (Math.abs(origin.top - stage.scrollTop) > 0.5 ||
          Math.abs(origin.left - stage.scrollLeft) > 0.5)
      ) {
        start.current = null;
        setDraft(null);
      }
    };
    stage.addEventListener("scroll", cancelMovedDraft, { passive: true });
    return () => stage.removeEventListener("scroll", cancelMovedDraft);
  }, []);
  const localPoint = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const drawAt = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return (
      start.current && dragRegion(page, start.current, localPoint(event), rect)
    );
  };
  return (
    <div
      ref={layer}
      role="group"
      className={`pdf-interaction-layer ${review.drawingFor ? "is-drawing" : ""}`}
      data-drawing-for={review.drawingFor?.id}
      tabIndex={review.drawingFor ? -1 : undefined}
      aria-label={
        review.drawingFor
          ? `Marcar trecho para ${review.drawingFor.description}`
          : undefined
      }
      onWheelCapture={() => {
        cancelHover();
        if (start.current) {
          start.current = null;
          setDraft(null);
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && review.drawingFor) {
          event.preventDefault();
          event.stopPropagation();
          start.current = null;
          setDraft(null);
          review.onCancelDrawing();
        }
      }}
      onPointerDown={(event) => {
        if (
          !review.drawingFor ||
          review.paused ||
          event.button !== 0 ||
          start.current
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        cancelHover();
        onGestureStart?.();
        const stage = event.currentTarget.closest<HTMLElement>(".pdf-stage");
        scrollOrigin.current = stage
          ? { top: stage.scrollTop, left: stage.scrollLeft }
          : null;
        start.current = { ...localPoint(event), pointerId: event.pointerId };
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (start.current && start.current.pointerId === event.pointerId) {
          setDraft(drawAt(event));
          return;
        }
        if (
          review.drawingFor ||
          review.paused ||
          event.pointerType === "touch" ||
          event.buttons
        )
          return;
        const previous = lastPointer.current;
        if (
          previous &&
          Math.hypot(previous.x - event.clientX, previous.y - event.clientY) < 2
        )
          return;
        lastPointer.current = { x: event.clientX, y: event.clientY };
        const direct = (event.target as HTMLElement).closest<HTMLElement>(
          "[data-pdf-target]",
        )?.dataset.pdfTarget;
        const targetId =
          direct ??
          targetAtPoint(
            targets,
            localPoint(event),
            event.currentTarget.getBoundingClientRect(),
          )?.transactionId;
        if (!targetId || targetId === review.selectedId) {
          cancelHover();
          return;
        }
        if (pendingTarget.current === targetId) return;
        cancelHover();
        pendingTarget.current = targetId;
        timer.current = setTimeout(() => {
          pendingTarget.current = null;
          timer.current = null;
          const current = latest.current;
          if (
            !current.paused &&
            !current.drawingFor &&
            current.selectedId !== targetId &&
            regionTargets(current.transactions, page).some(
              (target) => target.transactionId === targetId,
            )
          )
            current.onSelect(targetId, page);
        }, 110);
      }}
      onPointerLeave={() => {
        cancelHover();
        lastPointer.current = null;
      }}
      onPointerUp={(event) => {
        if (!start.current || start.current.pointerId !== event.pointerId)
          return;
        const region = drawAt(event);
        start.current = null;
        setDraft(null);
        if (event.currentTarget.hasPointerCapture(event.pointerId))
          event.currentTarget.releasePointerCapture(event.pointerId);
        if (region) review.onCreateRegion(region);
      }}
      onPointerCancel={() => {
        start.current = null;
        setDraft(null);
      }}
      onLostPointerCapture={() => {
        start.current = null;
        setDraft(null);
      }}
    >
      {!review.drawingFor &&
        !review.paused &&
        targets.map((target, index) => (
          <button
            key={`${target.transactionId}:${target.kind}:${index}`}
            type="button"
            className="pdf-region-target"
            data-pdf-target={target.transactionId}
            data-region-kind={target.kind}
            aria-label={`Selecionar ${target.description} no revisor (página ${page})`}
            title={
              target.kind === "manual"
                ? `Marcação de ${target.description}`
                : `Origem de ${target.description}`
            }
            aria-pressed={review.selectedId === target.transactionId}
            style={{
              left: `${target.region.left * 100}%`,
              top: `${target.region.top * 100}%`,
              width: `${target.region.width * 100}%`,
              height: `${target.region.height * 100}%`,
            }}
            onClick={() => {
              cancelHover();
              review.onSelect(target.transactionId, page);
            }}
            onFocus={() => {
              cancelHover();
              review.onSelect(target.transactionId, page);
            }}
          />
        ))}
      {draft && (
        <div
          className="pdf-manual-draft"
          style={{
            left: `${draft.left * 100}%`,
            top: `${draft.top * 100}%`,
            width: `${draft.width * 100}%`,
            height: `${draft.height * 100}%`,
          }}
        />
      )}
    </div>
  );
}
