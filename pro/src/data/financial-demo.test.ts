import { describe, expect, it } from "vitest";
import {
  financialDemoRepository,
  getFinancialScenario,
} from "./financial-demo";
import {
  assessClosing,
  projectDocumentReviews,
  projectMovements,
  scopedFindings,
} from "@/domain/financial-center";

describe("Autoridade da demonstração financeira", () => {
  it("confirma campos suspeitos sem modificar dados e invalida o aceite no próximo save", async () => {
    const scenario = structuredClone(getFinancialScenario("review")!);
    scenario.id = "review-confirmation-regression";
    const repository = financialDemoRepository(scenario);
    const before = await repository.getBatch(scenario.batch.id);
    const assess = (batch: typeof before) =>
      assessClosing(
        scenario,
        projectMovements(scenario, batch),
        "2026-09",
        ["inter"],
        true,
        "",
        projectDocumentReviews(scenario, batch),
      );
    expect(before.conversions[0].reviewStatus).toBe("PENDING");
    expect(assess(before).allowed).toBe(false);
    await repository.markReviewed(before.conversions[0].id);
    const accepted = await repository.getBatch(scenario.batch.id);
    expect(accepted.conversions[0].transactions).toEqual(
      before.conversions[0].transactions,
    );
    expect(
      accepted.conversions[0].transactions[1].fieldStates.description,
    ).toBe("SUSPECTED");
    expect(assess(accepted).allowed).toBe(true);
    await repository.saveTransactions(
      accepted.conversions[0].id,
      accepted.conversions[0].transactions,
    );
    expect(assess(await repository.getBatch(scenario.batch.id)).allowed).toBe(
      false,
    );
  });

  it("usa o mesmo repository para revisão e leitura, mantendo o PDF original", async () => {
    const scenario = getFinancialScenario("review")!;
    const repository = financialDemoRepository(scenario);
    const original = await repository.loadDocument!("review-doc");
    const batch = await repository.getBatch(scenario.batch.id);
    batch.conversions[0].transactions[1].description = "Descrição conferida";
    batch.conversions[0].transactions[1].fieldStates.description = "EDITED";
    await repository.saveTransactions(
      "review-doc",
      batch.conversions[0].transactions,
    );
    const reopened = await financialDemoRepository(scenario).getBatch(
      scenario.batch.id,
    );
    const rows = projectMovements(scenario, reopened);
    expect(rows[1].description).toBe("Descrição conferida");
    expect(scopedFindings(scenario, rows).length).toBeGreaterThan(0);
    await repository.markReviewed("review-doc");
    expect(
      scopedFindings(
        scenario,
        projectMovements(
          scenario,
          await repository.getBatch(scenario.batch.id),
        ),
      ),
    ).toEqual([]);
    expect(await repository.loadDocument!("review-doc")).toEqual(original);
    expect(scenario.batch.conversions[0].transactions[1].description).toBe(
      "Pagamento fornecedor",
    );
  });

  it("recusa documento expirado e IDs de outro cenário", async () => {
    const scenario = getFinancialScenario("expired")!;
    const repository = financialDemoRepository(scenario);
    await expect(repository.loadDocument!("expired-doc")).rejects.toThrow(
      "Documento indisponível",
    );
    await expect(repository.loadDocument!("single-doc")).rejects.toThrow(
      "Documento indisponível",
    );
  });

  it("preserva as duas pernas das sugestões de transferência e estorno", () => {
    const scenario = getFinancialScenario("multiple")!;
    const rows = projectMovements(scenario, scenario.batch);
    for (const id of ["transfer", "reversal"]) {
      const finding = scopedFindings(scenario, rows).find(
        (item) => item.id === id,
      )!;
      const pair = rows.filter((row) => finding.movementIds.includes(row.id));
      expect(pair).toHaveLength(2);
      expect(pair.reduce((sum, row) => sum + row.amountCents, 0)).toBe(0);
      expect(finding.kind).toBe("suggestion");
    }
  });
});
