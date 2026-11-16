import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { FiscalWorkspace } from "./fiscal-workspace";
import { fiscalCommand, loadFiscalWorkspace } from "@/data/fiscal-service";

vi.mock("@/data/fiscal-service", () => ({
  loadFiscalWorkspace: vi.fn(),
  fiscalCommand: vi.fn(),
  uploadFiscalNote: vi.fn(),
  downloadFiscal: vi.fn(),
}));
const fixture = {
  features: { invoices: true, taxes: true, revenue: true },
  documents: [],
  imports: [],
  links: [],
  suggestions: [],
  categories: {
    unclassified: "Não classificado",
    tax_simples: "Simples Nacional / DAS",
    operating_receipt: "Recebimento operacional",
  },
  movements: [
    {
      id: "m1",
      date: "2026-11-02",
      amount: "-50.00",
      currency: "BRL",
      description: "PAGAMENTO DAS",
      version: 1,
      possible_duplicate: false,
      classification: null,
      tax_suggestion: {
        category: "tax_simples",
        evidence: "Descrição bancária",
      },
      source: { job_id: "j1", batch_id: "b1", filename: "extrato.pdf" },
    },
  ],
  movement_count: 1,
  next_offset: null,
  access_expires_at: null,
  truncated: false,
  summary: {
    documented_sales: "100.00",
    operating_receipts: "80.00",
    unclassified_inflows: "20.00",
    excluded_inflows: "0.00",
    tax_payments: {},
    pending_documents: 0,
    coverage: "unknown",
    consolidation: "observed",
    basis: "Notas por emissão",
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadFiscalWorkspace).mockResolvedValue(fixture);
  vi.mocked(fiscalCommand).mockResolvedValue({});
});
it("mantém a sugestão tributária pendente até confirmação", async () => {
  render(
    <FiscalWorkspace clientId="c1" month="2026-11" account="" mode="taxes" />,
  );
  await screen.findByText("PAGAMENTO DAS");
  expect(fiscalCommand).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Categoria de PAGAMENTO DAS"), {
    target: { value: "tax_simples" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar classificação" }),
  );
  await waitFor(() =>
    expect(fiscalCommand).toHaveBeenCalledWith(
      "c1",
      "/classifications/m1",
      expect.objectContaining({
        category: "tax_simples",
        expected_version: 0,
        movement_version: 1,
      }),
    ),
  );
});
it("separa vendas documentadas e recebimentos sem somá-los", async () => {
  render(
    <FiscalWorkspace clientId="c1" month="2026-11" account="" mode="revenue" />,
  );
  await screen.findByText("Vendas documentadas");
  expect(
    screen.getByText("Recebimentos operacionais identificados"),
  ).toBeInTheDocument();
  expect(screen.getByText(/cobertura/i)).toBeInTheDocument();
  expect(screen.queryByText(/180,00/)).not.toBeInTheDocument();
});
it("remove os valores ao trocar para um cliente cujo acesso falha", async () => {
  const view = render(
    <FiscalWorkspace clientId="c1" month="2026-11" account="" mode="taxes" />,
  );
  await screen.findByText("PAGAMENTO DAS");
  vi.mocked(loadFiscalWorkspace).mockRejectedValue(
    new Error("Acesso expirado"),
  );
  view.rerender(
    <FiscalWorkspace clientId="c2" month="2026-11" account="" mode="taxes" />,
  );
  await screen.findByText("Acesso expirado");
  expect(screen.queryByText("PAGAMENTO DAS")).not.toBeInTheDocument();
});

it("vincula a nota somente depois de conferir e confirmar a sugestão", async () => {
  vi.mocked(loadFiscalWorkspace).mockResolvedValue({
    ...fixture,
    documents: [
      {
        id: "n1",
        version: 1,
        kind: "nfe",
        number: "123",
        series: "1",
        issued_on: "2026-11-01",
        competence_on: null,
        gross_amount: "50.00",
        net_amount: null,
        operation: "sale",
        status: "reported",
        direction: "received",
        issuer: { name: "Fornecedor", tax_id: "12345678000195" },
        recipient: { name: "Empresa", tax_id: "98765432000198" },
        taxes: {},
        review_required: false,
        source: { format: "xml", fields: {} },
      },
    ],
    suggestions: [
      {
        movement_id: "m1",
        movement_version: 1,
        amount: "50.00",
        confidence: "weak",
        evidence: ["amount", "date_window"],
        movement: fixture.movements[0],
      },
    ],
  });
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
    />,
  );
  fireEvent.change(await screen.findByLabelText("Nota fiscal"), {
    target: { value: "n1" },
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Conferir vínculo" }),
  );
  expect(fiscalCommand).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar vínculo" }));
  await waitFor(() =>
    expect(fiscalCommand).toHaveBeenCalledWith(
      "c1",
      "/fiscal/links",
      expect.objectContaining({
        document_id: "n1",
        movement_id: "m1",
        amount: "50.00",
        document_version: 1,
        movement_version: 1,
      }),
    ),
  );
});
