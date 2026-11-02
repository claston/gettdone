import { describe, expect, it } from "vitest";
import {
  assessClosing,
  calendarDays,
  filterMovements,
  projectMovements,
  reportCsv,
} from "./financial-center";
import { financialScenarios } from "@/data/financial-demo";

const scenario = (id: string) =>
  financialScenarios.find((item) => item.id === id)!;

describe("Central Financeira: projeções de demonstração", () => {
  it("distingue ausência de movimento de ausência de cobertura, sem inferir pelas datas", () => {
    const data = scenario("partial");
    const days = calendarDays(
      data,
      projectMovements(data, data.batch),
      "2026-09",
      ["inter"],
    );
    expect(days[0]).toMatchObject({ covered: true, count: 0 });
    expect(days[19]).toMatchObject({ covered: false, count: 0 });
    expect(days).toHaveLength(30);
    expect(calendarDays(data, [], "2028-02", ["inter"])).toHaveLength(29);
    expect(
      calendarDays(data, [], "2026-09", []).every((day) => !day.covered),
    ).toBe(true);
  });

  it("preserva ID, sinal, origem e repetições legítimas entre documentos", () => {
    const data = scenario("multiple");
    const rows = projectMovements(data, data.batch);
    expect(new Set(rows.map((row) => row.id)).size).toBe(rows.length);
    expect(new Set(rows.map((row) => row.source.batchId)).size).toBeGreaterThan(
      1,
    );
    expect(rows.some((row) => row.amountCents < 0)).toBe(true);
    expect(rows.every((row) => row.source.page === 1)).toBe(true);
    expect(
      rows.filter((row) => row.description === "Pagamento fornecedor"),
    ).toHaveLength(2);
  });

  it("projeta a versão atual do mesmo revisor, excluindo linhas removidas", () => {
    const data = scenario("single");
    const batch = structuredClone(data.batch);
    batch.conversions[0].transactions[0].description = "Correção no revisor";
    batch.conversions[0].transactions[1].isDeleted = true;
    const rows = projectMovements(data, batch);
    expect(rows[0].description).toBe("Correção no revisor");
    expect(rows).toHaveLength(2);
  });

  it("não divulga conteúdo financeiro ou cobertura de documentos expirados", () => {
    const data = scenario("expired");
    expect(projectMovements(data, data.batch)).toEqual([]);
    expect(
      calendarDays(data, [], "2026-09", ["inter"]).every((day) => !day.covered),
    ).toBe(true);
  });

  it("filtra por mês, conta, intervalo, descrição, direção e revisão", () => {
    const data = scenario("review");
    const rows = projectMovements(data, data.batch);
    const filtered = filterMovements(rows, {
      month: "2026-09",
      accountIds: ["inter"],
      from: "2026-09-02",
      to: "2026-09-03",
      description: "FORNECEDOR",
      direction: "debit",
      review: "pending",
    });
    expect(filtered).toHaveLength(1);
    expect(
      filterMovements(rows, { month: "2026-10", accountIds: ["inter"] }),
    ).toEqual([]);
    expect(filterMovements(rows, { month: "2026-09", accountIds: [] })).toEqual(
      [],
    );
  });

  it("exige escopo confirmado, ressalvas justificadas e bloqueia integridade", () => {
    const data = scenario("partial");
    const rows = projectMovements(data, data.batch);
    expect(
      assessClosing(data, rows, "2026-09", ["inter"], false, "").allowed,
    ).toBe(false);
    expect(
      assessClosing(data, rows, "2026-09", ["inter"], true, "").allowed,
    ).toBe(false);
    expect(
      assessClosing(
        data,
        rows,
        "2026-09",
        ["inter"],
        true,
        "Extrato cobre apenas a primeira quinzena.",
      ),
    ).toMatchObject({ allowed: true, withReservations: true });
    for (const id of ["review", "duplicates", "divergence"]) {
      const blocked = scenario(id);
      expect(
        assessClosing(
          blocked,
          projectMovements(blocked, blocked.batch),
          "2026-09",
          ["inter"],
          true,
          "Justificativa não remove bloqueios",
        ).allowed,
      ).toBe(false);
    }
    expect(
      assessClosing(data, [], "2026-10", ["inter"], true, "Sem extrato válido")
        .allowed,
    ).toBe(false);
    expect(
      assessClosing(data, rows, "2026-09", [], true, "Sem contas").allowed,
    ).toBe(false);
  });

  it("aceita extrato sem movimentos comprovado e ignora pendências fora do recorte", () => {
    const empty = scenario("empty");
    expect(
      assessClosing(empty, [], "2026-09", ["inter"], true, ""),
    ).toMatchObject({ allowed: true, withReservations: false });
    const multiple = scenario("multiple");
    expect(
      assessClosing(
        multiple,
        projectMovements(multiple, multiple.batch),
        "2026-09",
        ["itau"],
        true,
        "Contraparte da transferência fora do recorte.",
      ).allowed,
    ).toBe(true);
  });

  it("exporta recorte, origem, ressalvas e protege texto contra fórmulas de planilha", () => {
    const data = scenario("single");
    const rows = projectMovements(data, data.batch);
    rows[0] = { ...rows[0], description: '=HYPERLINK("teste")' };
    const csv = reportCsv(data, rows, "2026-09", ["inter"], "Não concluído");
    expect(csv).toContain("DEMONSTRAÇÃO");
    expect(csv).toContain("Origem");
    expect(csv).toContain("'=HYPERLINK");
    expect(csv).toContain("Não concluído");
    expect(csv).not.toContain("Saldo");
  });
});
