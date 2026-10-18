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
        onSourcePage: vi.fn(),
        onLocateTransaction: locate,
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
      expect(locate).not.toHaveBeenCalled();
      expect(container.scrollTop).toBe(before);
      act(() => input.blur());
      expect(container.scrollTop).toBeGreaterThan(before);
      fireEvent.click(
        screen.getByRole("button", { name: /Localizar origem de Linha 2/ }),
      );
      expect(locate).toHaveBeenCalledExactlyOnceWith("2");
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
      onLocateTransaction: vi.fn(),
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
  it("locates by stable ID and retains the chosen row on its continuation page", () => {
    const locate = vi.fn();
    const multi = {
      ...row("2", 1),
      sourceRegions: [
        { page: 1, left: 0.1, top: 0.8, width: 0.6, height: 0.03 },
        { page: 2, left: 0.1, top: 0.1, width: 0.6, height: 0.03 },
      ],
    };
    render(
      <TransactionGrid
        conversion={{ ...conversion, transactions: [multi] }}
        activePage={2}
        locatedTransactionId="2"
        groupByPage={false}
        onSourcePage={vi.fn()}
        onLocateTransaction={locate}
        onUpdate={vi.fn()}
        onGroupChange={vi.fn()}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
        onRestore={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTitle("Destacar origem no PDF"));
    expect(locate).toHaveBeenCalledExactlyOnceWith("2");
    expect(
      screen.queryByText(/sem lançamentos associados/),
    ).not.toBeInTheDocument();
  });
});
