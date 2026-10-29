import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReceivedStatementsPage } from "./received-statements-page";

const page = {
  totals: { statements: 2, ready: 1, attention: 1 },
  clients: [
    {
      id: "client_1",
      name: "Padaria São José",
      tax_id: "12345678000190",
      trade_name: "",
      updated_at: "v1",
      historical_count: 5,
      available_count: 2,
      expired_count: 3,
      attention_count: 1,
      accounts: [
        {
          id: "a1",
          bank_code: "341",
          account_number: "••1234",
          statement_count: 5,
        },
      ],
    },
  ],
  pending: [],
  next_cursor: null,
};

describe("Extratos recebidos", () => {
  it("mostra os três indicadores zerados com quatro extratos expirados no histórico", async () => {
    const service = {
      list: vi.fn().mockResolvedValue({
        ...page,
        totals: { statements: 0, ready: 0, attention: 0 },
        clients: [
          {
            ...page.clients[0],
            historical_count: 4,
            available_count: 0,
            expired_count: 4,
          },
        ],
      }),
      refresh: vi.fn().mockResolvedValue(undefined),
      edit: vi.fn(),
      confirm: vi.fn(),
    };
    render(<ReceivedStatementsPage service={service} />);
    expect(
      await screen.findByText("0 disponíveis / 4 extratos no histórico"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("Resumo dos extratos")).getAllByText("0"),
    ).toHaveLength(3);
    expect(screen.getByText("4 expirados")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Padaria São José/ }),
    ).not.toBeInTheDocument();
  });
  it("consolida por cliente, mantém expiração secundária e edita inline sem cadastro", async () => {
    const service = {
      list: vi.fn().mockResolvedValue(page),
      refresh: vi.fn().mockResolvedValue(undefined),
      edit: vi.fn().mockResolvedValue(undefined),
      confirm: vi.fn(),
    };
    render(<ReceivedStatementsPage service={service} />);
    expect(
      await screen.findByRole("heading", { name: "Extratos recebidos" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: /Padaria São José/ }),
    ).toHaveAttribute("href", "/client-review/client_1");
    expect(
      screen.getByText("2 disponíveis / 5 extratos no histórico"),
    ).toBeInTheDocument();
    expect(screen.getByText("3 expirados")).toBeInTheDocument();
    expect(screen.getByText(/Itaú/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Cadastrar|Incluir cliente/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Editar Padaria São José" }),
    );
    fireEvent.change(screen.getByLabelText("Nome do cliente"), {
      target: { value: "Padaria corrigida" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Salvar identificação" }),
    );
    await waitFor(() =>
      expect(service.edit).toHaveBeenCalledWith(
        "client_1",
        expect.objectContaining({
          name: "Padaria corrigida",
          expected_updated_at: "v1",
        }),
      ),
    );
  });

  it("mantém extratos expirados sem link de revisão e mostra identificação pendente", async () => {
    const service = {
      list: vi.fn().mockResolvedValue({
        ...page,
        totals: { statements: 1, ready: 1, attention: 0 },
        clients: [{ ...page.clients[0], available_count: 0, expired_count: 5 }],
        pending: [
          {
            job_id: "j2",
            batch_id: "b2",
            filename: "pendente.pdf",
            version: 0,
            source: {
              holder_name: "Nome extraído",
              references: [{ page: 1, line: 2 }],
            },
          },
        ],
      }),
      refresh: vi.fn().mockResolvedValue(undefined),
      edit: vi.fn(),
      confirm: vi.fn(),
    };
    render(<ReceivedStatementsPage service={service} />);
    expect(
      await screen.findByRole("heading", { name: "Identificação pendente" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Padaria São José/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Conferir pendente.pdf" }),
    ).toHaveAttribute("href", "/review/b2/j2?return_to=%2Fdashboard");
  });
});
