import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  financialDemoRepository,
  getFinancialScenario,
} from "@/data/financial-demo";
import { FinancialCenter } from "./financial-center";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

describe("Jornada da Central Financeira", () => {
  it("bloqueia documento sem lançamentos até a confirmação explícita de revisão", async () => {
    const scenario = structuredClone(getFinancialScenario("single")!);
    scenario.id = "deleted-rows-review";
    const repository = financialDemoRepository(scenario);
    const batch = await repository.getBatch(scenario.batch.id);
    const conversion = batch.conversions[0];
    await repository.saveTransactions(
      conversion.id,
      conversion.transactions.map((row) => ({ ...row, isDeleted: true })),
    );

    const view = render(<FinancialCenter scenario={scenario} />);
    fireEvent.click(await screen.findByRole("button", { name: "Fechamento" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Confirmo estas contas/ }),
    );
    expect(
      screen.getByRole("button", { name: "Simular fechamento" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("link", { name: "Revisar documento" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining(`/review/${conversion.id}?returnTo=`),
    );
    view.unmount();

    await repository.markReviewed(conversion.id);
    render(<FinancialCenter scenario={scenario} />);
    fireEvent.click(await screen.findByRole("button", { name: "Fechamento" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Confirmo estas contas/ }),
    );
    expect(
      screen.getByRole("button", { name: "Simular fechamento" }),
    ).toBeEnabled();
  });

  it("aceita suspeitas conferidas sem exigir alteração dos valores", async () => {
    const scenario = structuredClone(getFinancialScenario("review")!);
    scenario.id = "confirmed-suspected-fields";
    const repository = financialDemoRepository(scenario);
    const before = await repository.getBatch(scenario.batch.id);
    await repository.markReviewed(before.conversions[0].id);
    const after = await repository.getBatch(scenario.batch.id);
    expect(after.conversions[0].transactions).toEqual(
      before.conversions[0].transactions,
    );

    render(<FinancialCenter scenario={scenario} />);
    fireEvent.click(await screen.findByRole("button", { name: "Lançamentos" }));
    fireEvent.change(screen.getByLabelText("Revisão"), {
      target: { value: "pending" },
    });
    expect(
      screen.queryByRole("link", { name: "Abrir no revisor" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Fechamento" }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Confirmo estas contas/ }),
    );
    expect(
      screen.getByRole("button", { name: "Simular fechamento" }),
    ).toBeEnabled();
  });

  it("abre um dia, mantém contexto nas áreas e filtra a lista unificada", async () => {
    render(<FinancialCenter scenario={getFinancialScenario("multiple")!} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: "03/09/2026: 2 movimentações, com pendências",
      }),
    );
    expect(screen.getByText("Lançamentos de 03/09/2026")).toBeInTheDocument();
    expect(
      screen.getAllByRole("link", { name: "Abrir no revisor" }),
    ).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Lançamentos" }));
    fireEvent.change(screen.getByLabelText("Descrição"), {
      target: { value: "transferência" },
    });
    expect(
      screen.getAllByRole("link", { name: "Abrir no revisor" }),
    ).toHaveLength(2);
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Itaú · Conta ••5678" }),
    );
    expect(
      screen.getAllByRole("link", { name: "Abrir no revisor" }),
    ).toHaveLength(1);
    expect(
      screen
        .getByRole("link", { name: "Abrir no revisor" })
        .getAttribute("href"),
    ).toContain("returnTo=");
    fireEvent.click(screen.getByRole("button", { name: "Próximo mês" }));
    expect(screen.getByText("outubro de 2026")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Abrir no revisor" }),
    ).not.toBeInTheDocument();
  });

  it("conclui apenas a simulação com ressalvas e invalida a confirmação ao trocar escopo", async () => {
    render(<FinancialCenter scenario={getFinancialScenario("partial")!} />);
    fireEvent.click(await screen.findByRole("button", { name: "Fechamento" }));
    expect(
      screen.getByRole("button", { name: "Simular fechamento com ressalvas" }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Confirmo estas contas/ }),
    );
    fireEvent.change(
      screen.getByLabelText("Justificativa para este mês e contas"),
      { target: { value: "Inter: segunda quinzena não recebida." } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Simular fechamento com ressalvas" }),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Concluído com ressalvas (simulação)",
    );
    fireEvent.click(screen.getByRole("button", { name: "Relatórios" }));
    expect(
      screen.getByText(/Concluído com ressalvas \(simulação\):/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próximo mês" }));
    expect(screen.getByText("Não concluído")).toBeInTheDocument();
  });

  it("não oferece abertura ou exportação de conteúdo expirado", async () => {
    render(<FinancialCenter scenario={getFinancialScenario("expired")!} />);
    await screen.findByRole("button", {
      name: "01/09/2026: 0 movimentações, sem cobertura suficiente",
    });
    expect(
      screen.getByText(/conteúdo financeiro e PDF indisponíveis/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Lançamentos" }));
    expect(
      screen.queryByRole("link", { name: "Abrir no revisor" }),
    ).not.toBeInTheDocument();
  });

  it("reabre cliente, mês, contas e aba do endereço de retorno", async () => {
    render(
      <FinancialCenter
        scenario={getFinancialScenario("multiple")!}
        initialQuery="month=2026-09&accounts=itau&tab=Lançamentos"
      />,
    );
    await waitFor(() =>
      expect(
        screen.getAllByRole("link", { name: "Abrir no revisor" }),
      ).toHaveLength(2),
    );
    expect(
      screen.getByRole("checkbox", { name: "Inter · Conta ••1234" }),
    ).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Lançamentos" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
