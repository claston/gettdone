import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentsPage } from "./documents-page";
import type { DocumentsService, DocumentItem } from "@/data/documents-service";
import type { OrganizationService } from "@/data/organization-service";
import { receivedService } from "@/data/received-statements-service";

vi.mock("@/data/received-statements-service", () => ({
  receivedService: { confirm: vi.fn().mockResolvedValue({}), refresh: vi.fn() },
}));
beforeEach(() => vi.clearAllMocks());

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("../financial/fiscal-workspace", () => ({
  FiscalWorkspace: () => <p>Notas do cliente</p>,
}));
const catalog = {
  clients: [
    { id: "client_a", name: "Cliente A", tax_id: null, trade_name: "" },
  ],
  accounts: [],
  periods: [],
};
const file: DocumentItem = {
  job_id: "job_a",
  batch_id: "batch_a",
  filename: "documento.pdf",
  category: "statements",
  status: "completed",
  is_expired: false,
  created_at: "2026-11-21T12:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  movement_start_date: null,
  movement_end_date: null,
  review_url: "/review/batch_a/job_a",
  context: { client_id: null, account_id: null, period_ids: [], version: 0 },
};
function services(items = [file]) {
  return {
    service: {
      list: vi.fn().mockResolvedValue({ items, next_cursor: null }),
    } as DocumentsService,
    organization: {
      catalog: vi.fn().mockResolvedValue(catalog),
      organize: vi.fn().mockResolvedValue({}),
    } as unknown as OrganizationService,
  };
}

describe("Documentos", () => {
  it.each(["all", "invoices"] as const)(
    "usa a importação comum sem painel fiscal separado em %s",
    async (category) => {
      render(
        <DocumentsPage
          {...services()}
          clientId="client_a"
          category={category}
        />,
      );
      await screen.findByRole("link", { name: "Abrir revisão" });
      expect(screen.queryByText("Notas do cliente")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("region", { name: "Notas fiscais importadas" }),
      ).not.toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Importar documentos" }),
      );
      expect(
        screen.getByRole("region", { name: "Importação de documentos" }),
      ).toBeVisible();
    },
  );
  it("abre um único painel de importação e mantém notas fiscais na categoria própria", async () => {
    render(
      <DocumentsPage
        {...services()}
        clientId="client_a"
        category="statements"
      />,
    );
    expect(
      screen.getByRole("button", { name: "Importar documentos" }),
    ).toBeDisabled();
    await screen.findByRole("link", { name: "Abrir revisão" });
    fireEvent.click(
      screen.getByRole("button", { name: "Importar documentos" }),
    );
    const panel = screen.getByRole("region", {
      name: "Importação de documentos",
    });
    expect(screen.getByLabelText("Cliente")).toBeDisabled();
    expect(within(panel).getAllByRole("heading")).toHaveLength(1);
    expect(
      within(panel).getByRole("heading", { name: "Importar documentos" }),
    ).toBeVisible();
    expect(
      within(panel).getByText(/Cliente selecionado: Cliente A/),
    ).toBeVisible();
    expect(
      within(panel).getByText(/Os arquivos serão associados a este cliente/),
    ).toBeVisible();
    expect(
      within(panel).queryByRole("link", { name: /nota fiscal/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Notas fiscais" })).toHaveAttribute(
      "href",
      "/documents?client=client_a&type=invoices",
    );
    expect(
      screen.getAllByRole("button", { name: "Importar documentos" }),
    ).toHaveLength(1);
    fireEvent.click(
      within(panel).getByRole("button", { name: "Fechar importação" }),
    );
    expect(
      screen.queryByRole("region", { name: "Importação de documentos" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Importar documentos" }),
    ).toBeVisible();
    expect(screen.getByLabelText("Cliente")).toBeEnabled();
  });
  it("filtra no servidor e retorna da revisão ao cliente e tipo selecionados", async () => {
    const props = services();
    render(
      <DocumentsPage
        {...props}
        clientId="client_a"
        category="statements"
        financialCenterVisible
      />,
    );
    expect(
      await screen.findByRole("link", { name: "Abrir revisão" }),
    ).toHaveAttribute(
      "href",
      "/review/batch_a/job_a?return_to=%2Fdocuments%3Fclient%3Dclient_a%26type%3Dstatements",
    );
    expect(props.service.list).toHaveBeenCalledWith(
      "category=statements&client_id=client_a",
      expect.any(AbortSignal),
    );
    expect(
      screen.getByRole("link", { name: "Central Financeira" }),
    ).toHaveAttribute("href", "/financial/client_a");
    fireEvent.change(screen.getByLabelText("Cliente"), {
      target: { value: "unassigned" },
    });
    expect(push).toHaveBeenCalledWith(
      "/documents?client=unassigned&type=statements",
    );
  });
  it("exige confirmação e preserva a versão ao associar arquivos do envio", async () => {
    const props = services();
    render(<DocumentsPage {...props} clientId="client_a" batchId="batch_a" />);
    const button = await screen.findByRole("button", {
      name: "Confirmar associação ao cliente",
    });
    expect(receivedService.confirm).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() =>
      expect(receivedService.confirm).toHaveBeenCalledWith("job_a", {
        client_id: "client_a",
        name: "Cliente A",
        tax_id: null,
        expected_version: 0,
      }),
    );
  });
  it("não exibe um documento expirado nem oferece associação ou revisão", async () => {
    const props = services([
      {
        ...file,
        expires_at: "2020-01-01T00:00:00Z",
        context: { ...file.context, client_id: "client_other" },
      },
    ]);
    render(<DocumentsPage {...props} clientId="client_a" batchId="batch_a" />);
    await screen.findByText(/Nenhum documento encontrado/);
    expect(screen.queryByText(file.filename)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Confirmar associação ao cliente" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Abrir revisão" }),
    ).not.toBeInTheDocument();
    expect(props.organization.organize).not.toHaveBeenCalled();
    expect(receivedService.confirm).not.toHaveBeenCalled();
  });
  it("aguarda a leitura antes de oferecer a confirmação do titular", async () => {
    const props = services([{ ...file, status: "running" }]);
    render(<DocumentsPage {...props} clientId="client_a" batchId="batch_a" />);
    await screen.findByText("Processando");
    expect(
      screen.queryByRole("button", { name: "Confirmar associação ao cliente" }),
    ).not.toBeInTheDocument();
    expect(receivedService.confirm).not.toHaveBeenCalled();
  });
  it("mantém a próxima página quando a página filtrada está vazia", async () => {
    const props = services([]);
    vi.mocked(props.service.list).mockResolvedValue({
      items: [],
      next_cursor: "next",
    });
    render(<DocumentsPage {...props} category="receipts" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Próxima página" }),
    );
    await waitFor(() =>
      expect(props.service.list).toHaveBeenLastCalledWith(
        "category=receipts&cursor=next",
        expect.any(AbortSignal),
      ),
    );
  });
});
