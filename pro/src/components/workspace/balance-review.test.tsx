import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { BalanceCheck, Conversion, Transaction } from "@/domain/workspace";
import { BalanceReviewNotice, TransactionReviewNote } from "./balance-review";

const check: BalanceCheck = {
  referenceBalance: 1000,
  creditsTotal: 0,
  debitsTotal: 100,
  expectedBalance: 900,
  reportedBalance: 880,
  difference: -20,
  movementsCount: 1,
  order: "ascending",
  referenceDate: "01/08/2026",
  referencePage: 2,
};
const transaction: Transaction = {
  id: "synthetic",
  description: "LANÇAMENTO SINTÉTICO",
  balanceCheck: check,
  warningTypes: ["balance_consistency_failed"],
  date: "02/08/2026",
  credit: null,
  debit: 100,
  balance: 880,
  fieldStates: {
    date: "ORIGINAL",
    description: "ORIGINAL",
    credit: "ORIGINAL",
    debit: "ORIGINAL",
    balance: "SUSPECTED",
  },
};

it("mostra no topo o motivo e a conta que não fecha", () => {
  render(
    <BalanceReviewNotice
      conversion={
        {
          transactions: [transaction],
          statementBalanceCheck: check,
        } as Conversion
      }
    />,
  );
  expect(screen.getByText(/1 lançamento com saldo divergente/)).toBeVisible();
  expect(screen.getByText(/Saldo final não confere/)).toBeVisible();
  expect(screen.getByText(/Saldo registrado/)).toHaveTextContent("880,00");
  expect(screen.getByText(/Saldo esperado/)).toHaveTextContent("900,00");
  expect(screen.getByText(/Diferença/)).toHaveTextContent("20,00");
});

it("explica na linha o saldo usado, os movimentos e a diferença", () => {
  render(<TransactionReviewNote transaction={transaction} />);
  fireEvent.click(screen.getByText(/Saldo não confere/));
  expect(screen.getByText(/Saldo de referência/)).toHaveTextContent("1.000,00");
  expect(screen.getByText(/01\/08\/2026/)).toHaveTextContent("pág. 2");
  expect(screen.getByText(/Saldo esperado/)).toHaveTextContent("900,00");
  expect(screen.getByText(/Saldo registrado/)).toHaveTextContent("880,00");
});

it("explica avisos antigos sem inventar valores de comparação", () => {
  render(
    <TransactionReviewNote
      transaction={{ ...transaction, balanceCheck: undefined }}
    />,
  );
  expect(screen.getByText(/Saldo não confere/)).toHaveTextContent(/Confira/);
  expect(screen.queryByText(/Saldo esperado/)).not.toBeInTheDocument();
});

it("avisa quando é preciso desfazer movimentos na ordem decrescente", () => {
  render(
    <TransactionReviewNote
      transaction={{
        ...transaction,
        balanceCheck: { ...check, order: "descending" },
      }}
    />,
  );
  fireEvent.click(screen.getByText(/Saldo não confere/));
  expect(screen.getByText(/desfaz os movimentos/i)).toBeVisible();
});
