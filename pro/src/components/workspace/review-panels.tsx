"use client";

import {
  Children,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

export function ReviewPanels({ children }: { children: ReactNode }) {
  const container = useRef<HTMLDivElement>(null);
  const [share, setShare] = useState(45);
  const [collapsed, setCollapsed] = useState<"pdf" | "data" | null>(null);
  const panels = Children.toArray(children);
  const changeShare = (value: number) => {
    setCollapsed(null);
    setShare(Math.max(25, Math.min(75, value)));
  };
  return (
    <div
      ref={container}
      className="review-stage resizable-panels"
      data-collapsed={collapsed ?? undefined}
      style={
        {
          "--pdf-width": `${share}fr`,
          "--data-width": `${100 - share}fr`,
        } as CSSProperties
      }
    >
      {panels[0]}
      <div className="panel-divider">
        <button
          type="button"
          aria-label={collapsed === "pdf" ? "Mostrar PDF" : "Recolher PDF"}
          title={collapsed === "pdf" ? "Mostrar PDF" : "Recolher PDF"}
          onClick={() =>
            setCollapsed((value) => (value === "pdf" ? null : "pdf"))
          }
        >
          ‹
        </button>
        <div
          role="separator"
          aria-label="Ajustar largura do PDF e dos lançamentos"
          aria-orientation="vertical"
          aria-valuemin={25}
          aria-valuemax={75}
          aria-valuenow={Math.round(share)}
          tabIndex={0}
          title="Arraste ou use as setas para ajustar. Clique duplo restaura."
          onDoubleClick={() => changeShare(45)}
          onKeyDown={(event) => {
            const value = {
              ArrowLeft: share - 5,
              ArrowRight: share + 5,
              Home: 25,
              End: 75,
              Enter: 45,
            }[event.key];
            if (value !== undefined) {
              event.preventDefault();
              changeShare(value);
            }
          }}
          onPointerDown={(event) => {
            event.preventDefault();
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
            const bounds = container.current?.getBoundingClientRect();
            if (bounds?.width)
              changeShare(((event.clientX - bounds.left) / bounds.width) * 100);
          }}
          onPointerUp={(event) =>
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
        >
          ⋮
        </div>
        <button
          type="button"
          aria-label={
            collapsed === "data"
              ? "Mostrar lançamentos"
              : "Recolher lançamentos"
          }
          title={
            collapsed === "data"
              ? "Mostrar lançamentos"
              : "Recolher lançamentos"
          }
          onClick={() =>
            setCollapsed((value) => (value === "data" ? null : "data"))
          }
        >
          ›
        </button>
      </div>
      {panels[1]}
    </div>
  );
}
