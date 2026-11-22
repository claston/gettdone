import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ClientsPage } from "./clients-page";
import type { OrganizationService } from "@/data/organization-service";
import { receivedService } from "@/data/received-statements-service";

afterEach(() => vi.restoreAllMocks());

function editingService() {
  const clients = [
    {
      id: "first",
      name: "Ana",
      tax_id: null,
      trade_name: "",
      updated_at: "v1",
    },
    {
      id: "second",
      name: "Bruno",
      tax_id: "12345678901",
      trade_name: "",
      updated_at: "v2",
    },
  ];
  const catalog = { clients, accounts: [], periods: [] };
  const service = {
    catalog: vi.fn().mockResolvedValue(catalog),
    saveClient: vi.fn(),
  } as unknown as OrganizationService;
  return { service, catalog };
}

describe("Carteira de clientes", () => {
  it("lista também clientes sem documentos e abre Documentos no contexto correto", async () => {
    const service = {
      catalog: vi.fn().mockResolvedValue({
        clients: [
          {
            id: "client_empty",
            name: "Sem documentos",
            tax_id: null,
            trade_name: "",
          },
        ],
        accounts: [],
        periods: [],
      }),
    } as unknown as OrganizationService;
    render(<ClientsPage service={service} financialCenterVisible />);
    expect(
      await screen.findByRole("link", { name: "Sem documentos" }),
    ).toHaveAttribute("href", "/documents?client=client_empty");
    expect(screen.getByRole("link", { name: "Extratos" })).toHaveAttribute(
      "href",
      "/documents?client=client_empty&type=statements",
    );
    expect(
      screen.getByRole("link", { name: "Central Financeira" }),
    ).toHaveAttribute("href", "/financial/client_empty");
    expect(screen.queryByText("Com conversões ativas")).not.toBeInTheDocument();
  });
  it("cadastra um cliente sem criar uma conversão", async () => {
    const service = {
      catalog: vi
        .fn()
        .mockResolvedValue({ clients: [], accounts: [], periods: [] }),
      saveClient: vi.fn().mockResolvedValue({ id: "client_new" }),
    } as unknown as OrganizationService;
    render(<ClientsPage service={service} />);
    await screen.findByText("Nenhum cliente cadastrado.");
    fireEvent.click(screen.getByRole("button", { name: "Novo cliente" }));
    expect(
      screen.getByLabelText("Nome do cliente").closest("article"),
    ).toBeNull();
    fireEvent.change(screen.getByLabelText("Nome do cliente"), {
      target: { value: "Cliente novo" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar cliente" }));
    await waitFor(() =>
      expect(service.saveClient).toHaveBeenCalledWith({
        name: "Cliente novo",
        tax_id: null,
        trade_name: "",
      }),
    );
  });

  it("abre a edição no cliente escolhido e cancela sem alterar o cadastro", async () => {
    const { service } = editingService();
    const edit = vi.spyOn(receivedService, "edit").mockResolvedValue({});
    render(<ClientsPage service={service} />);
    const button = await screen.findByRole("button", { name: "Editar Bruno" });
    const row = button.closest("article")!;
    fireEvent.click(button);
    const name = within(row).getByLabelText("Nome do cliente");
    expect(name).toHaveValue("Bruno");
    expect(name).toHaveFocus();
    expect(within(row).getByLabelText("CPF/CNPJ (opcional)")).toHaveValue(
      "12345678901",
    );
    fireEvent.change(name, { target: { value: "Rascunho" } });
    fireEvent.click(within(row).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByLabelText("Nome do cliente")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
    expect(edit).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(within(row).getByLabelText("Nome do cliente")).toHaveValue("Bruno");
    fireEvent.click(screen.getByRole("button", { name: "Editar Ana" }));
    expect(screen.getAllByLabelText("Nome do cliente")).toHaveLength(1);
    expect(
      within(row).queryByLabelText("Nome do cliente"),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Nome do cliente")).toHaveValue("Ana");
  });

  it("salva o cliente correto com controle de versão e atualiza a lista", async () => {
    const { service, catalog } = editingService();
    const edit = vi
      .spyOn(receivedService, "edit")
      .mockImplementation(async () => {
        vi.mocked(service.catalog).mockResolvedValue({
          ...catalog,
          clients: catalog.clients.map((client) =>
            client.id === "second"
              ? { ...client, name: "Bruno atualizado", updated_at: "v3" }
              : client,
          ),
        });
      });
    render(<ClientsPage service={service} />);
    const button = await screen.findByRole("button", { name: "Editar Bruno" });
    const row = button.closest("article")!;
    fireEvent.click(button);
    fireEvent.change(within(row).getByLabelText("Nome do cliente"), {
      target: { value: " Bruno atualizado " },
    });
    fireEvent.click(
      within(row).getByRole("button", { name: "Salvar cliente" }),
    );
    expect(
      await screen.findByRole("link", { name: "Bruno atualizado" }),
    ).toBeVisible();
    expect(edit).toHaveBeenCalledExactlyOnceWith("second", {
      name: "Bruno atualizado",
      tax_id: "12345678901",
      expected_updated_at: "v2",
    });
    expect(service.saveClient).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Nome do cliente")).not.toBeInTheDocument();
  });

  it("mantém a edição e o erro na linha quando não é possível salvar", async () => {
    const { service } = editingService();
    vi.spyOn(receivedService, "edit").mockRejectedValue(
      new Error("Não foi possível salvar o cliente. Tente novamente."),
    );
    render(<ClientsPage service={service} />);
    const button = await screen.findByRole("button", { name: "Editar Bruno" });
    const row = button.closest("article")!;
    fireEvent.click(button);
    fireEvent.change(within(row).getByLabelText("Nome do cliente"), {
      target: { value: "Bruno atualizado" },
    });
    fireEvent.click(
      within(row).getByRole("button", { name: "Salvar cliente" }),
    );
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "Não foi possível salvar o cliente.",
    );
    expect(within(row).getByLabelText("Nome do cliente")).toHaveValue(
      "Bruno atualizado",
    );
    expect(
      within(row).getByRole("button", { name: "Salvar cliente" }),
    ).toBeEnabled();
  });
});
