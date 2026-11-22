import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClientsPage } from "./clients-page";
import type { OrganizationService } from "@/data/organization-service";

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
});
