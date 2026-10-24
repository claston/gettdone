import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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
  it("não apresenta emissão como vencimento quando o título não informa data de vencimento", () => {
    const report: FinancialDocument = {
      ...fiscal,
      document_type: "accounts_receivable_report",
      events: [
        {
          ...fiscal.events[0],
          event_type: "receivable_title",
          dates: { issue: "2026-07-01", payment: "2026-07-12" },
        },
      ],
    };
    render(<FinancialReportReview report={report} />);
    expect(
      screen.getByRole("columnheader", { name: "Emissão" }),
    ).toBeInTheDocument();
    expect(screen.getByText("01/07/2026")).toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Vencimento" }),
    ).not.toBeInTheDocument();
  });

  it.each(["payable", "receivable"])(
    "exibe títulos %s com vencimento, contraparte e saldo sem OFX",
    (kind) => {
      const report: FinancialDocument = {
        ...fiscal,
        document_type:
          kind === "payable"
            ? "accounts_payable_report"
            : "accounts_receivable_report",
        events: [
          {
            ...fiscal.events[0],
            event_type: `${kind}_title`,
            status: "partial",
            amount: "100.00",
            dates: { due: "2026-07-10", payment: "2026-07-12" },
            counterparty: "EMPRESA TESTE",
            amounts: { outstanding: "30.00" },
          },
        ],
      };
      render(
        <FinancialReportReview
          report={report}
          downloadUrl="/convert-report/an_titles?format=xlsx"
        />,
      );
      expect(
        screen.getByRole("columnheader", { name: "Vencimento" }),
      ).toBeInTheDocument();
      expect(screen.getByText("10/07/2026")).toBeInTheDocument();
      expect(screen.getByText("EMPRESA TESTE")).toBeInTheDocument();
      expect(screen.getByText("Parcial")).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: "Baixar CSV" }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: "Baixar Excel" }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Exportar OFX" }),
      ).not.toBeInTheDocument();
    },
  );

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
    fireEvent.click(screen.getByRole("button", { name: "Exportar OFX" }));
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

  it("organiza o relatório em painel próprio e mantém referências junto à descrição", () => {
    const onSource = vi.fn();
    render(<FinancialReportReview report={fiscal} onSource={onSource} />);
    expect(
      screen.getByRole("heading", { name: "Resumo fiscal" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("columnheader", { name: "Referência" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Referência: 0001")).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Ver origem do evento Documento fiscal 0001",
      }),
    );
    expect(onSource).toHaveBeenCalledWith("event-1");
  });
});
