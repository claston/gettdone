import type { Batch } from "./workspace";

// Read-only view contracts, not a second editable transaction model or an API DTO.
// A future adapter must read the authorized canonical SQL snapshot (OFX-64/70).
export interface FinancialAccount {
  id: string;
  label: string;
}
export interface FinancialSource {
  conversionId: string;
  batchId: string;
  accountId: string;
  filename: string;
  availability: "available" | "expired";
  coverage: { from: string; to: string } | null;
}
export type FindingKind =
  "review" | "suggestion" | "unmatched" | "duplicate" | "divergence";
export interface FinancialFinding {
  id: string;
  kind: FindingKind;
  title: string;
  detail: string;
  movementIds: readonly string[];
  blocking: boolean;
}
export interface FinancialScenario {
  id: string;
  label: string;
  client: { name: string; taxId: string };
  month: string;
  trial?: boolean;
  accounts: readonly FinancialAccount[];
  sources: readonly FinancialSource[];
  findings: readonly FinancialFinding[];
  batch: Batch;
}
export interface FinancialMovement {
  readonly id: string;
  readonly date: string;
  readonly description: string;
  readonly amountCents: number;
  readonly accountId: string;
  readonly pending: boolean;
  readonly source: FinancialSource & { page: number | null };
}
export interface FinancialDocumentReview {
  readonly source: FinancialSource;
  readonly reviewed: boolean;
}
export interface MovementFilters {
  month: string;
  accountIds: readonly string[];
  from?: string;
  to?: string;
  description?: string;
  direction?: "all" | "credit" | "debit";
  review?: "all" | "pending" | "reviewed";
}
export const money = (cents: number) =>
  (cents / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
export const dateLabel = (date: string) => date.split("-").reverse().join("/");
export function monthLabel(month: string) {
  return new Date(`${month}-01T12:00:00Z`).toLocaleDateString("pt-BR", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}
export function shiftMonth(month: string, delta: number) {
  const date = new Date(`${month}-01T12:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + delta);
  return date.toISOString().slice(0, 7);
}

export function projectMovements(
  scenario: FinancialScenario,
  batch: Batch,
): FinancialMovement[] {
  return batch.conversions
    .flatMap((conversion) => {
      const source = scenario.sources.find(
        (item) => item.conversionId === conversion.id,
      );
      if (!source || source.availability !== "available") return [];
      return conversion.transactions
        .filter((row) => !row.isDeleted)
        .map((row) => ({
          id: row.id,
          date: row.date.includes("/")
            ? row.date.split("/").reverse().join("-")
            : row.date,
          description: row.description,
          amountCents: Math.round(((row.credit ?? 0) - (row.debit ?? 0)) * 100),
          accountId: source.accountId,
          // Explicit document acceptance covers unchanged suspected fields.
          // Subsequent saves invalidate reviewStatus in the shared repository.
          pending: conversion.reviewStatus !== "REVIEWED",
          source: { ...source, page: row.sourcePage ?? null },
        }));
    })
    .sort((a, b) => a.date.localeCompare(b.date) || a.id.localeCompare(b.id));
}

export function projectDocumentReviews(
  scenario: FinancialScenario,
  batch: Batch,
): FinancialDocumentReview[] {
  return scenario.sources.map((source) => ({
    source,
    reviewed:
      batch.conversions.find(
        (conversion) => conversion.id === source.conversionId,
      )?.reviewStatus === "REVIEWED",
  }));
}

export function scopedDocumentReviews(
  documents: readonly FinancialDocumentReview[],
  rows: readonly FinancialMovement[],
  month: string,
  accountIds: readonly string[],
) {
  const from = `${month}-01`;
  const until = `${shiftMonth(month, 1)}-01`;
  return documents.filter(
    ({ source }) =>
      source.availability === "available" &&
      accountIds.includes(source.accountId) &&
      ((source.coverage &&
        source.coverage.from < until &&
        source.coverage.to >= from) ||
        rows.some(
          (row) =>
            row.source.conversionId === source.conversionId &&
            row.date >= from &&
            row.date < until,
        )),
  );
}

const normalize = (value: string) =>
  value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-BR");
export function filterMovements(
  rows: readonly FinancialMovement[],
  filters: MovementFilters,
) {
  return rows.filter(
    (row) =>
      row.date.startsWith(`${filters.month}-`) &&
      filters.accountIds.includes(row.accountId) &&
      (!filters.from || row.date >= filters.from) &&
      (!filters.to || row.date <= filters.to) &&
      (!filters.description ||
        normalize(row.description).includes(normalize(filters.description))) &&
      (filters.direction !== "credit" || row.amountCents > 0) &&
      (filters.direction !== "debit" || row.amountCents < 0) &&
      (filters.review !== "pending" || row.pending) &&
      (filters.review !== "reviewed" || !row.pending),
  );
}
export function totals(rows: readonly FinancialMovement[]) {
  return rows.reduce(
    (sum, row) => ({
      count: sum.count + 1,
      credits: sum.credits + Math.max(0, row.amountCents),
      debits: sum.debits + Math.max(0, -row.amountCents),
    }),
    { count: 0, credits: 0, debits: 0 },
  );
}
export function scopedFindings(
  scenario: FinancialScenario,
  rows: readonly FinancialMovement[],
) {
  const ids = new Set(rows.map((row) => row.id));
  const findings = scenario.findings.filter(
    (finding) =>
      finding.movementIds.some((id) => ids.has(id)) &&
      (finding.kind !== "review" ||
        rows.some(
          (row) => finding.movementIds.includes(row.id) && row.pending,
        )),
  );
  const additionalReview = rows.filter(
    (row) =>
      row.pending &&
      !findings.some(
        (finding) =>
          finding.kind === "review" && finding.movementIds.includes(row.id),
      ),
  );
  if (additionalReview.length)
    findings.push({
      id: "pending-review",
      kind: "review",
      title: "Lançamentos precisam de revisão",
      detail:
        "A versão atual do documento precisa ser conferida no revisor antes do fechamento.",
      movementIds: additionalReview.map((row) => row.id),
      blocking: true,
    });
  return findings;
}
export function calendarDays(
  scenario: FinancialScenario,
  rows: readonly FinancialMovement[],
  month: string,
  accountIds: readonly string[],
) {
  const count = new Date(
    Number(month.slice(0, 4)),
    Number(month.slice(5, 7)),
    0,
  ).getDate();
  const scoped = filterMovements(rows, { month, accountIds });
  return Array.from({ length: count }, (_, index) => {
    const date = `${month}-${String(index + 1).padStart(2, "0")}`;
    const movements = scoped.filter((row) => row.date === date);
    const covered =
      accountIds.length > 0 &&
      accountIds.every((id) =>
        scenario.sources.some(
          (source) =>
            source.accountId === id &&
            source.availability === "available" &&
            source.coverage &&
            source.coverage.from <= date &&
            source.coverage.to >= date,
        ),
      );
    return {
      date,
      covered,
      ...totals(movements),
      pending:
        movements.some((row) => row.pending) ||
        scopedFindings(scenario, movements).length > 0,
    };
  });
}
export function assessClosing(
  scenario: FinancialScenario,
  allRows: readonly FinancialMovement[],
  month: string,
  accountIds: readonly string[],
  confirmed: boolean,
  reservation: string,
  documentReviews: readonly FinancialDocumentReview[],
) {
  const rows = filterMovements(allRows, { month, accountIds });
  const findings = scopedFindings(scenario, rows);
  const days = calendarDays(scenario, allRows, month, accountIds);
  const blockers: string[] = [];
  if (!accountIds.length) blockers.push("Selecione ao menos uma conta.");
  if (!confirmed) blockers.push("Confirme explicitamente as contas incluídas.");
  const hasDocument = scenario.sources.some(
    (source) =>
      accountIds.includes(source.accountId) &&
      source.availability === "available" &&
      source.coverage &&
      source.coverage.from <= days[days.length - 1].date &&
      source.coverage.to >= days[0].date,
  );
  if (!hasDocument)
    blockers.push("É necessário ao menos um extrato válido no mês.");
  if (rows.some((row) => row.pending))
    blockers.push("Há lançamentos que precisam de revisão.");
  const pendingDocuments = scopedDocumentReviews(
    documentReviews,
    allRows,
    month,
    accountIds,
  ).filter((document) => !document.reviewed);
  if (pendingDocuments.length)
    blockers.push(
      "Há documentos que precisam de revisão, mesmo sem lançamentos no recorte.",
    );
  blockers.push(
    ...findings
      .filter((finding) => finding.blocking)
      .map((finding) => finding.title),
  );
  const withReservations =
    days.some((day) => !day.covered) ||
    findings.some((finding) => !finding.blocking);
  if (withReservations && !reservation.trim())
    blockers.push("Documente a ressalva para este mês e estas contas.");
  return {
    allowed: blockers.length === 0,
    blockers,
    withReservations,
    pendingDocuments,
  };
}

// CSV for Excel: UTF-8 BOM, semicolon, quoted text and formula neutralization.
function csvCell(value: string | number) {
  let text =
    typeof value === "number" ? String(value).replace(".", ",") : value;
  if (typeof value === "string" && /^[\s]*[=+@-]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}
export function reportCsv(
  scenario: FinancialScenario,
  allRows: readonly FinancialMovement[],
  month: string,
  accountIds: readonly string[],
  closing: string,
) {
  const rows = filterMovements(allRows, { month, accountIds });
  const summary = totals(rows);
  const lines: (string | number)[][] = [
    ["DEMONSTRAÇÃO — dados fictícios, totais provisórios"],
    ["Cliente", scenario.client.name, scenario.client.taxId],
    ["Mês", month],
    [
      "Contas",
      scenario.accounts
        .filter((account) => accountIds.includes(account.id))
        .map((account) => account.label)
        .join(" / "),
    ],
    [
      "Cobertura",
      calendarDays(scenario, allRows, month, accountIds).every(
        (day) => day.covered,
      )
        ? "Documentada"
        : "Parcial ou ausente — não representa todo o mês",
    ],
    ["Fechamento simulado", closing],
    ["Entradas", money(summary.credits), "Saídas", money(summary.debits)],
    [
      "Data",
      "Descrição",
      "Valor BRL",
      "Conta",
      "Revisão",
      "Origem",
      "Lote de origem",
      "Página",
      "ID",
    ],
    ...rows.map((row) => [
      dateLabel(row.date),
      row.description,
      row.amountCents / 100,
      scenario.accounts.find((account) => account.id === row.accountId)!.label,
      row.pending ? "Revisão necessária" : "Conferido",
      row.source.filename,
      row.source.batchId,
      row.source.page ?? "Não informada",
      row.id,
    ]),
    [],
    ["Resumo por conta", "Entradas", "Saídas", "Movimentações"],
    ...scenario.accounts
      .filter((account) => accountIds.includes(account.id))
      .map((account) => {
        const sum = totals(rows.filter((row) => row.accountId === account.id));
        return [
          account.label,
          money(sum.credits),
          money(sum.debits),
          sum.count,
        ];
      }),
    [],
    ["Pendências", "Detalhe"],
    ...scopedFindings(scenario, rows).map((finding) => [
      finding.title,
      finding.detail,
    ]),
  ];
  return (
    "\uFEFF" + lines.map((line) => line.map(csvCell).join(";")).join("\r\n")
  );
}
