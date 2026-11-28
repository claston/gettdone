"use client";

import Link from "next/link";
import {
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import styles from "./month-filter.module.css";

const months = [
  "Janeiro",
  "Fevereiro",
  "Março",
  "Abril",
  "Maio",
  "Junho",
  "Julho",
  "Agosto",
  "Setembro",
  "Outubro",
  "Novembro",
  "Dezembro",
];

export function MonthFilter({
  month,
  onChange,
  allHref,
  unknownHref,
  allLabel = "Todos os meses",
}: {
  month: string;
  onChange(month: string): void;
  allHref: string;
  unknownHref: string;
  allLabel?: string;
}) {
  const selected = /^\d{4}-(0[1-9]|1[0-2])$/.test(month) ? month : "";
  const [open, setOpen] = useState(false);
  const [year, setYear] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const id = useId();
  const validYear =
    /^\d{1,4}$/.test(year) && Number(year) >= 1 && Number(year) <= 9999;
  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const selectedButton = dialog.current?.querySelector<HTMLButtonElement>(
      '[aria-pressed="true"]',
    );
    (
      selectedButton ??
      dialog.current?.querySelector<HTMLButtonElement>("[data-month]")
    )?.focus();
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  return (
    <div className={styles.filter}>
      <div
        className={styles.picker}
        ref={root}
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget))
            setOpen(false);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && open) {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
        }}
      >
        <span id={`${id}-label`} className={styles.label}>
          Mês de trabalho
        </span>
        <button
          type="button"
          ref={trigger}
          className={styles.trigger}
          aria-label="Mês de trabalho"
          aria-describedby={`${id}-value`}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? `${id}-dialog` : undefined}
          onClick={() => {
            if (!open)
              setYear(
                selected
                  ? selected.slice(0, 4)
                  : String(new Date().getFullYear()),
              );
            setOpen(!open);
          }}
        >
          <CalendarDays size={18} aria-hidden="true" />
          <span id={`${id}-value`}>
            {selected
              ? `${months[Number(selected.slice(5)) - 1]} de ${selected.slice(0, 4)}`
              : "Selecionar mês"}
          </span>
          <ChevronDown size={16} aria-hidden="true" />
        </button>
        {open && (
          <div
            ref={dialog}
            id={`${id}-dialog`}
            role="dialog"
            aria-label="Escolher mês"
            className={styles.popover}
          >
            <div className={styles.year}>
              <button
                type="button"
                aria-label="Ano anterior"
                disabled={!validYear || Number(year) === 1}
                onClick={() => setYear(String(Number(year) - 1))}
              >
                <ChevronLeft size={18} aria-hidden="true" />
              </button>
              <input
                aria-label="Ano"
                type="number"
                inputMode="numeric"
                min={1}
                max={9999}
                value={year}
                onChange={(event) => setYear(event.target.value)}
              />
              <button
                type="button"
                aria-label="Próximo ano"
                disabled={!validYear || Number(year) === 9999}
                onClick={() => setYear(String(Number(year) + 1))}
              >
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </div>
            <div className={styles.months}>
              {months.map((name, index) => {
                const value = `${year.padStart(4, "0")}-${String(index + 1).padStart(2, "0")}`;
                return (
                  <button
                    type="button"
                    key={name}
                    data-month
                    aria-pressed={selected === value}
                    disabled={!validYear}
                    onClick={() => {
                      onChange(value);
                      close();
                    }}
                  >
                    {name}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <nav className={styles.alternatives} aria-label="Outros períodos">
        <Link href={allHref} aria-current={!month ? "page" : undefined}>
          {allLabel}
        </Link>
        <Link
          href={unknownHref}
          aria-current={month === "unknown" ? "page" : undefined}
        >
          Período a confirmar
        </Link>
      </nav>
    </div>
  );
}
