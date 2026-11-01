import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReceivedStatementsPage } from "./received-statements-page";
import type { ReceivedPage } from "@/data/received-statements-service";

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

describe("Clientes", () => {
  it("busca no servidor, reinicia a paginação e mantém documentos pendentes independentes", async () => {
    const service = {
      list: vi
        .fn()
        .mockImplementation((cursor, _signal, _pendingCursor, query) =>
          Promise.resolve({
            ...page,
            clients: query === "inexistente" ? [] : page.clients,
            client_count: query === "inexistente" ? 0 : 1,
            pending_count: 1,
            next_cursor: cursor ? null : "c2",
            pending: [
              {
                job_id: "j2",
                batch_id: "b2",
                filename: "pendente.pdf",
                version: 0,
                source: {
                  bank_code: "341",
                  account_number: "123456",
                  confidence: "ambiguous",
                },
              },
            ],
          }),
        ),
      refresh: vi.fn().mockResolvedValue(undefined),
      edit: vi.fn(),
      confirm: vi.fn(),
    };
    render(<ReceivedStatementsPage service={service} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );
    expect(
      screen.getByRole("tab", { name: "Identificados (1)" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Próxima" }));
    await waitFor(() =>
      expect(service.list).toHaveBeenLastCalledWith(
        "c2",
        expect.any(AbortSignal),
        undefined,
        "",
      ),
    );
    const input = screen.getByRole("searchbox", {
      name: "Buscar por nome, CPF ou CNPJ",
    });
    fireEvent.change(input, { target: { value: "inexistente" } });
    await screen.findByText("Nenhum cliente encontrado");
    expect(service.list).toHaveBeenLastCalledWith(
      undefined,
      expect.any(AbortSignal),
      undefined,
      "inexistente",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Não identificados (1)" }));
    expect(
      screen.getByRole("link", { name: "Conferir pendente.pdf" }),
    ).toBeVisible();
    expect(screen.getByText(/Itaú/)).toBeVisible();
    expect(screen.getByText(/••3456/)).toBeVisible();
    expect(screen.getByText(/Identificação ambígua/)).toBeVisible();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Identificados (0)" }));
    expect(screen.getByRole("searchbox")).toHaveValue("inexistente");
    fireEvent.click(screen.getByRole("button", { name: "Limpar busca" }));
    await screen.findByRole("link", { name: /Padaria São José/ });
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });
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
      await screen.findByRole("heading", { name: "Clientes" }),
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
    fireEvent.click(
      await screen.findByRole("tab", { name: /^Não identificados/ }),
    );
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

  it("mostra carregamento desde a primeira consulta e mantém os dados durante toda a atualização", async () => {
    let finishList!: (value: ReceivedPage) => void;
    let finishRefresh!: () => void;
    const service = {
      list: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<ReceivedPage>((resolve) => {
              finishList = resolve;
            }),
        )
        .mockResolvedValue(page),
      refresh: vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<void>((resolve) => {
              finishRefresh = resolve;
            }),
        )
        .mockResolvedValue(undefined),
      edit: vi.fn(),
      confirm: vi.fn(),
    };
    render(<ReceivedStatementsPage service={service} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Carregando extratos...",
    );
    expect(
      screen.getByRole("button", { name: /Atualizando extratos/ }),
    ).toBeDisabled();
    finishList(page);
    await screen.findByRole("link", { name: /Padaria São José/ });
    expect(
      screen.getByRole("button", { name: /Atualizando extratos/ }),
    ).toBeDisabled();
    finishRefresh();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );

    service.list.mockImplementationOnce(
      () =>
        new Promise<ReceivedPage>((resolve) => {
          finishList = resolve;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Atualizar extratos" }));
    expect(
      screen.getByRole("button", { name: /Atualizando extratos/ }),
    ).toBeDisabled();
    expect(
      screen.getByRole("link", { name: /Padaria São José/ }),
    ).toBeVisible();
    finishList(page);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );
  });

  it("separa as listas em abas acessíveis, ordena nomes com acentos e não consulta novamente ao trocar de aba", async () => {
    const service = {
      list: vi.fn().mockResolvedValue({
        ...page,
        clients: [
          { ...page.clients[0], id: "z", name: "Zulu" },
          { ...page.clients[0], id: "b", name: "beatriz" },
          { ...page.clients[0], id: "a", name: "Álvaro" },
        ],
      }),
      refresh: vi.fn().mockResolvedValue(undefined),
      edit: vi.fn(),
      confirm: vi.fn(),
    };
    render(<ReceivedStatementsPage service={service} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );
    const clients = screen.getByRole("tab", { name: /^Identificados/ });
    for (const tab of screen.getAllByRole("tab")) {
      expect(
        document.getElementById(tab.getAttribute("aria-controls")!),
      ).toHaveAttribute("role", "tabpanel");
    }
    expect(clients).toHaveAttribute("aria-selected", "true");
    expect(
      within(screen.getByRole("tabpanel", { name: /^Identificados/ }))
        .getAllByRole("link")
        .map((link) => link.textContent?.split("CNPJ")[0]),
    ).toEqual(["Álvaro", "beatriz", "Zulu"]);
    fireEvent.keyDown(clients, { key: "ArrowRight" });
    const pending = screen.getByRole("tab", { name: /^Não identificados/ });
    expect(pending).toHaveFocus();
    expect(pending).toHaveAttribute("aria-selected", "true");
    expect(
      screen.queryByRole("link", { name: /Álvaro/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("Todos os extratos estão identificados."),
    ).toBeVisible();
    fireEvent.keyDown(pending, { key: "Home" });
    expect(clients).toHaveFocus();
    expect(clients).toHaveAttribute("aria-selected", "true");
    expect(service.list).toHaveBeenCalledTimes(2);
  });

  it("preserva a aba e a página de cada lista ao navegar e ao confirmar uma identificação", async () => {
    const pending = {
      job_id: "j2",
      batch_id: "b2",
      filename: "pendente.pdf",
      version: 0,
      source: { holder_name: "Novo cliente" },
    };
    const service = {
      list: vi.fn().mockImplementation((cursor, _signal, pendingCursor) =>
        Promise.resolve({
          ...page,
          pending: pendingCursor ? [pending] : [],
          next_cursor: cursor ? null : "c2",
          pending_next_cursor: pendingCursor ? null : "p2",
        }),
      ),
      refresh: vi.fn().mockResolvedValue(undefined),
      edit: vi.fn(),
      confirm: vi.fn().mockResolvedValue(undefined),
    };
    render(<ReceivedStatementsPage service={service} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Próxima" }));
    await waitFor(() =>
      expect(service.list).toHaveBeenLastCalledWith(
        "c2",
        expect.any(AbortSignal),
        undefined,
        "",
      ),
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("tab", { name: /^Não identificados/ }));
    fireEvent.click(
      screen.getByRole("button", { name: "Próximas identificações" }),
    );
    await screen.findByRole("link", { name: "Conferir pendente.pdf" });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );
    expect(service.list).toHaveBeenLastCalledWith(
      "c2",
      expect.any(AbortSignal),
      "p2",
      "",
    );
    fireEvent.click(screen.getByRole("tab", { name: /^Identificados/ }));
    expect(screen.getByRole("button", { name: "Início" })).toBeEnabled();
    fireEvent.click(screen.getByRole("tab", { name: /^Não identificados/ }));
    fireEvent.click(
      screen.getByRole("button", { name: "Conferir identificação" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Salvar identificação" }),
    );
    await waitFor(() =>
      expect(service.confirm).toHaveBeenCalledWith(
        "j2",
        expect.objectContaining({ name: "Novo cliente" }),
      ),
    );
    await waitFor(() =>
      expect(service.list).toHaveBeenLastCalledWith(
        "c2",
        expect.any(AbortSignal),
        undefined,
        "",
      ),
    );
    expect(
      screen.getByRole("tab", { name: /^Não identificados/ }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("encerra o carregamento em erro e permite tentar novamente", async () => {
    const service = {
      list: vi
        .fn()
        .mockRejectedValueOnce(new Error("Falha temporária"))
        .mockResolvedValue(page),
      refresh: vi.fn().mockResolvedValue(undefined),
      edit: vi.fn(),
      confirm: vi.fn(),
    };
    render(<ReceivedStatementsPage service={service} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Falha temporária",
    );
    expect(
      screen.queryByText("Carregando extratos..."),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await screen.findByRole("link", { name: /Padaria São José/ });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Atualizar extratos" }),
      ).toBeEnabled(),
    );
  });
});
