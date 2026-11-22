import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { Conversion, FinancialDocument } from "@/domain/workspace";
import { DocumentEvidenceReview } from "./document-evidence-review";
import { DocumentTypeControl } from "./document-type-control";

const report: FinancialDocument = {
  document_type: "bank_receipt",
  origin: null,
  notice: "Este documento não acrescenta uma movimentação ao extrato.",
  events: [],
  validations: [],
  export: {
    available_formats: ["xlsx", "csv"],
    requires_destination_account: false,
  },
  observations: [
    {
      observation_type: "document_field",
      dates: {},
      amounts: { value: "4189.59" },
      attributes: {
        label: "Valor da operação",
        value: "valor da transferência: R$ 4.189,59",
      },
      source_refs: [
        { page: 1, line: 9 },
        { page: 1, line: 11 },
      ],
    },
  ],
};

it("confere o comprovante com origem e sem validações de saldo", () => {
  render(<DocumentEvidenceReview report={report} />);
  expect(
    screen.getByRole("heading", { name: "Conferir comprovante" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/R\$ 4.189,59/)).toBeInTheDocument();
  expect(
    screen.getByText("Pág. 1, linha 9; Pág. 1, linha 11"),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/Saldo inicial|Saldo final|Conciliado/),
  ).not.toBeInTheDocument();
});

it("nota sem campos mantém a incerteza explícita e o acesso ao original", () => {
  render(
    <DocumentEvidenceReview
      report={{
        ...report,
        document_type: "fiscal_invoice",
        observations: [],
        notice: "O arquivo não confirma pagamento nem consulta à Receita.",
      }}
    />,
  );
  expect(
    screen.getByRole("heading", { name: "Conferir nota fiscal" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/não confirma pagamento/)).toBeInTheDocument();
  expect(
    screen.getByText(/Não foi possível identificar os campos com segurança/),
  ).toBeInTheDocument();
});

it("apresenta o impacto antes de confirmar a alteração e mantém erros visíveis", async () => {
  const onChange = vi
    .fn()
    .mockRejectedValue(new Error("A revisão mudou. Recarregue."));
  const conversion = {
    id: "job",
    pdf: { url: "/original" },
    documentType: "bank_statement",
    status: "READY",
  } as Conversion;
  render(
    <DocumentTypeControl
      conversion={conversion}
      disabled={false}
      onChange={onChange}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Alterar tipo" }));
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "bank_receipt" },
  });
  expect(
    screen.getByText(/deixam de compor os totais bancários/),
  ).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar alteração" }));
  await waitFor(() => expect(onChange).toHaveBeenCalledWith("bank_receipt"));
  expect(await screen.findByRole("alert")).toHaveTextContent("A revisão mudou");
});
