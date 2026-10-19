import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { FinancialDocument } from "@/domain/workspace";
import { FinancialReportReview } from "./financial-report-review";

const fiscal: FinancialDocument = {
  document_type: "fiscal_summary",
  origin: "GDOOR",
  notice: "Emissão fiscal não comprova recebimento.",
  validations: [{ code: "fiscal_total", status: "passed" }],
  export: {
    available_formats: ["csv", "xlsx"],
    requires_destination_account: false,
    reason: "OFX indisponível.",
  },
  events: [
    {
      event_id: "event-1",
      event_type: "fiscal_document",
      reference: "0001",
      status: "voided",
      description: "Documento fiscal 0001",
      dates: { issue: "2026-09-01" },
      amount: "0.00",
      cash_effect: null,
      source_refs: [{ page: 1, line: 4 }],
    },
  ],
};

describe("FinancialReportReview", () => {
  it("apresenta registros fiscais sem crédito bancário, edição ou OFX", () => {
    render(
      <FinancialReportReview
        report={fiscal}
        downloadUrl="/convert-report/an_fiscal?format=xlsx"
      />,
    );
    expect(
      screen.getByText("Emissão fiscal não comprova recebimento."),
    ).toBeInTheDocument();
    expect(screen.getByText("Inutilizado")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Baixar CSV" })).toHaveAttribute(
      "href",
      "/convert-report/an_fiscal?format=csv",
    );
    expect(
      screen.queryByRole("link", { name: "Baixar OFX" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Crédito" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("exige conta de destino para OFX de recebimentos, sem informar saldo", () => {
    const report = {
      ...fiscal,
      document_type: "receivables_report",
      export: {
        ...fiscal.export,
        available_formats: ["csv", "xlsx", "ofx"],
        requires_destination_account: true,
      },
    };
    render(
      <FinancialReportReview
        report={report}
        downloadUrl="/convert-report/an_titles?format=xlsx"
      />,
    );
    expect(
      screen.queryByRole("link", { name: "Baixar OFX" }),
    ).not.toBeInTheDocument();
    for (const [label, value] of [
      ["Banco de destino", "237"],
      ["Agência de destino", "1234"],
      ["Conta de destino", "56789"],
    ]) {
      fireEvent.change(screen.getByRole("textbox", { name: label }), {
        target: { value },
      });
    }
    const link = screen.getByRole("link", { name: "Baixar OFX" });
    expect(link).toHaveAttribute(
      "href",
      "/convert-report/an_titles?format=ofx&bank_code=237&bank_branch=1234&account_number=56789",
    );
    expect(link.getAttribute("href")).not.toContain("closing_balance");
  });
});
