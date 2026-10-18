import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Conversion, Transaction } from "@/domain/workspace";
import { TransactionGrid } from "./transaction-grid";

const row = (id: string, sourcePage?: number): Transaction => ({
  id,
  sourcePage,
  sourceSequence: Number(id),
  date: "18/10/2026",
  description: `Linha ${id}`,
  credit: 10,
  debit: null,
  balance: null,
  fieldStates: {
    date: "ORIGINAL",
    description: "ORIGINAL",
    credit: "ORIGINAL",
    debit: "ORIGINAL",
    balance: "ORIGINAL",
  },
});
const conversion: Conversion = {
  id: "doc",
  batchId: "batch",
  fileName: "synthetic.pdf",
  bankName: "Teste",
  period: "",
  status: "READY",
  reviewStatus: "PENDING",
  transactionCount: 3,
  suspectedCount: 0,
  editedCount: 0,
  pdf: { url: "/synthetic.pdf", pageCount: 4 },
  transactions: [row("1", 1), row("2", 2), row("3")],
};

describe("grid page anchors", () => {
  it.each([false, true])(
    "follows visible provenance in layout grouped=%s, preserving focus and edits",
    (groupByPage) => {
      const locate = vi.fn();
      const update = vi.fn();
      const props = {
        conversion,
        activePage: 1,
        groupByPage,
        onSourcePage: locate,
        onUpdate: update,
        onGroupChange: vi.fn(),
        onAdd: vi.fn(),
        onRemove: vi.fn(),
        onRestore: vi.fn(),
      };
      const { rerender } = render(<TransactionGrid {...props} />);
      expect(
        screen.queryByRole("checkbox", {
          name: "Sincronizar PDF e lançamentos",
        }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/Acompanhando página/)).not.toBeInTheDocument();
      const container = document.querySelector(".table-scroll") as HTMLElement;
      const second = screen.getByDisplayValue("Linha 2").closest("tr")!;
      vi.spyOn(second, "getBoundingClientRect").mockReturnValue({
        top: 400,
      } as DOMRect);
      const input = screen.getByDisplayValue("Linha 1");
      act(() => input.focus());
      fireEvent.change(input, { target: { value: "Edição preservada" } });
      const before = container.scrollTop;
      rerender(<TransactionGrid {...props} activePage={2} />);
      expect(input).toHaveFocus();
      expect(container.scrollTop).toBe(before);
      act(() => input.blur());
      expect(container.scrollTop).toBeGreaterThan(before);
      fireEvent.click(
        screen.getByRole("button", { name: /Localizar origem de Linha 2/ }),
      );
      expect(locate).toHaveBeenCalledExactlyOnceWith(2);
      expect(screen.getByDisplayValue("Edição preservada")).toBeInTheDocument();
      expect(update).toHaveBeenCalledTimes(1);
    },
  );
  it("reports missing and filtered pages without removing filters or inventing origins", () => {
    const props = {
      conversion,
      activePage: 4,
      groupByPage: false,
      onSourcePage: vi.fn(),
      onUpdate: vi.fn(),
      onGroupChange: vi.fn(),
      onAdd: vi.fn(),
      onRemove: vi.fn(),
      onRestore: vi.fn(),
    };
    const { rerender } = render(<TransactionGrid {...props} />);
    expect(
      screen.getByText(/Página 4: sem lançamentos associados/),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Sem página de origem disponível"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Suspeitos/ }));
    rerender(<TransactionGrid {...props} activePage={2} />);
    expect(
      screen.getByText(/Página 2: nenhum lançamento visível neste filtro/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(1);
  });
});
