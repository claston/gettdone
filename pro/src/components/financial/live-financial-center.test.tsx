import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  loadFinancialMonth,
  type FinancialMonth,
} from "@/data/financial-center-service";
import { LiveFinancialCenter } from "./live-financial-center";
import { loadFiscalWorkspace, uploadFiscalNote } from "@/data/fiscal-service";

vi.mock("@/data/financial-center-service", async (original) => ({
  ...(await original<typeof import("@/data/financial-center-service")>()),
  loadFinancialMonth: vi.fn(),
}));
vi.mock("@/data/fiscal-service", async (original) => ({
  ...(await original<typeof import("@/data/fiscal-service")>()),
  loadFiscalWorkspace: vi.fn(),
  uploadFiscalNote: vi.fn(),
}));
const result: FinancialMonth = {
  client: { id: "client_test", name: "Cliente real", tax_id: "••••1234" },
  month: "2026-04",
  accounts: [{ id: "account_test", label: "341 · Conta ••1234" }],
  days: [
    {
      date: "2026-04-02",
      count: 1,
      pending_count: 1,
      coverage: "unknown",
      currency: "BRL",
      inflows: "0.00",
      outflows: "2.75",
      consolidation: "observed",
    },
  ],
  movements: [
    {
      id: "movement",
      date: "2026-04-02",
      amount: "-2.75",
      currency: "BRL",
      description: "Descrição revisada",
      account_id: "account_test",
      version: 2,
      review_state: "pending",
      pending: true,
      possible_duplicate: false,
      source: {
        analysis_id: "analysis",
        job_id: "job_test",
        batch_id: "batch_test",
        filename: "extrato.pdf",
        page: 2,
        analysis_version: 2,
        availability: "available",
      },
    },
  ],
  next_cursor: null,
  coverage: "unknown",
  financial_reconciliation: "not_implemented",
  unavailable_sources: 0,
  legacy_sources: 0,
  access_expires_at: null,
};
beforeEach(() => {
  vi.mocked(loadFinancialMonth).mockReset().mockResolvedValue(result);
  vi.mocked(loadFiscalWorkspace).mockReset();
  vi.mocked(uploadFiscalNote).mockReset();
  window.history.replaceState(null, "", "/");
});
describe("Central canônica", () => {
  it.each(["upload", "cancel"])(
    "preserva o seletor e retoma a atualização após a seleção (%s)",
    async (outcome) => {
      vi.mocked(loadFinancialMonth).mockResolvedValue({
        ...result,
        features: { invoices: true, taxes: false, revenue: false },
      });
      vi.mocked(loadFiscalWorkspace).mockResolvedValue({
        features: { invoices: true, taxes: false, revenue: false },
        documents: [],
        imports: [],
        movements: [],
        links: [],
        suggestions: [],
        categories: {},
        movement_count: 0,
        next_offset: null,
        access_expires_at: null,
        truncated: false,
        summary: {
          documented_sales: null,
          operating_receipts: null,
          unclassified_inflows: null,
          excluded_inflows: null,
          tax_payments: {},
          pending_documents: 0,
          coverage: "unknown",
          consolidation: "observed",
          basis: "",
        },
      });
      render(
        <LiveFinancialCenter
          clientId="client_test"
          initialQuery="month=2026-11&tab=Concilia%C3%A7%C3%A3o"
        />,
      );
      const input = await screen.findByLabelText("Importar nota XML ou PDF");
      fireEvent.click(input);
      await act(async () => {
        fireEvent.focus(window);
      });
      expect(input).toBeInTheDocument();
      expect(loadFinancialMonth).toHaveBeenCalledTimes(1);
      expect(loadFiscalWorkspace).toHaveBeenCalledTimes(1);
      if (outcome === "upload") {
        let finish!: () => void;
        vi.mocked(uploadFiscalNote).mockReturnValue(
          new Promise((resolve) => {
            finish = () => resolve({ id: "i1", state: "queued" });
          }),
        );
        const file = new File(["<NFe/>"], "arroz.xml");
        fireEvent.change(input, { target: { files: [file] } });
        await act(async () => {
          fireEvent.focus(window);
        });
        expect(input).toBeInTheDocument();
        expect(input).toBeDisabled();
        expect(uploadFiscalNote).toHaveBeenCalledWith(
          "client_test",
          file,
          expect.any(AbortSignal),
        );
        expect(loadFinancialMonth).toHaveBeenCalledTimes(1);
        await act(async () => finish());
      } else {
        await act(async () => {
          fireEvent(input, new Event("cancel"));
        });
      }
      await act(async () => {
        fireEvent.focus(window);
      });
      await waitFor(() => expect(loadFinancialMonth).toHaveBeenCalledTimes(2));
    },
  );
  it.each([
    [1, 0, "1 extrato da empresa não está incluído nesta consulta."],
    [2, 0, "2 extratos da empresa não estão incluídos nesta consulta."],
    [0, 1, "1 extrato da empresa não está incluído nesta consulta."],
    [0, 3, "3 extratos da empresa não estão incluídos nesta consulta."],
    [1, 1, "Há extratos da empresa que não estão incluídos nesta consulta."],
  ])(
    "explica o impacto dos extratos ausentes e oferece uma ação (%i, %i)",
    async (legacy, unavailable, message) => {
      vi.mocked(loadFinancialMonth).mockResolvedValue({
        ...result,
        legacy_sources: legacy,
        unavailable_sources: unavailable,
      });
      render(<LiveFinancialCenter clientId="client_test" />);
      await screen.findByRole("heading", { name: "Cliente real" });
      const notice = screen.getByRole("status");
      expect(notice).toHaveTextContent(message);
      expect(notice).toHaveTextContent("Os totais podem estar incompletos.");
      expect(notice).not.toHaveTextContent(
        /conversor anterior|origem|suspens|0 extrato/,
      );
      expect(
        screen.getByRole("link", { name: "Conferir extratos em Clientes" }),
      ).toHaveAttribute("href", "/dashboard");
    },
  );
  it("não exibe aviso de extratos ausentes quando as contagens são zero", async () => {
    render(<LiveFinancialCenter clientId="client_test" />);
    await screen.findByRole("heading", { name: "Cliente real" });
    expect(screen.queryByText("Os totais podem estar incompletos.")).toBeNull();
    expect(
      screen.queryByRole("link", { name: "Conferir extratos em Clientes" }),
    ).toBeNull();
  });
  it("aguarda a resolução do mês sem publicar um período provisório", async () => {
    let resolve!: (data: FinancialMonth) => void;
    vi.mocked(loadFinancialMonth).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    render(
      <LiveFinancialCenter
        clientId="client_test"
        initialQuery="accounts=account_test&pending=true"
      />,
    );
    expect(screen.getByRole("button", { name: "Mês anterior" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Próximo mês" })).toBeDisabled();
    expect(window.location.search).toBe("");
    await act(async () => resolve(result));
    expect(new URLSearchParams(window.location.search).get("month")).toBe(
      "2026-04",
    );
    expect(loadFinancialMonth).toHaveBeenCalledTimes(1);
    expect(vi.mocked(loadFinancialMonth).mock.calls[0][1]).toContain(
      "account=account_test",
    );
  });
  it("permite repetir a resolução inicial quando a consulta falha", async () => {
    vi.mocked(loadFinancialMonth).mockRejectedValueOnce(
      new Error("Consulta indisponível"),
    );
    render(<LiveFinancialCenter clientId="client_test" />);
    await screen.findByRole("alert");
    expect(loadFinancialMonth).toHaveBeenCalledTimes(1);
    expect(window.location.search).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await screen.findByRole("heading", { name: "Cliente real" });
    // The heading can render before the effect synchronizes the URL.
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("month")).toBe(
        "2026-04",
      ),
    );
    expect(loadFinancialMonth).toHaveBeenCalledTimes(2);
  });
  it.each(["", "month=invalid"])(
    "resolve o último mês com movimentos ao entrar (%s)",
    async (initialQuery) => {
      render(
        <LiveFinancialCenter
          clientId="client_test"
          initialQuery={initialQuery}
        />,
      );
      await screen.findByRole("heading", { name: "Cliente real" });
      expect(
        new URLSearchParams(vi.mocked(loadFinancialMonth).mock.calls[0][1]).has(
          "month",
        ),
      ).toBe(false);
      await waitFor(() =>
        expect(new URLSearchParams(window.location.search).get("month")).toBe(
          "2026-04",
        ),
      );
      expect(loadFinancialMonth).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
      await waitFor(() =>
        expect(vi.mocked(loadFinancialMonth).mock.lastCall?.[1]).toContain(
          "month=2026-04",
        ),
      );
      fireEvent.click(screen.getByRole("button", { name: "Próximo mês" }));
      await waitFor(() =>
        expect(vi.mocked(loadFinancialMonth).mock.lastCall?.[1]).toContain(
          "month=2026-05",
        ),
      );
    },
  );
  it("usa o mês atual retornado pelo servidor quando não há movimentos", async () => {
    vi.mocked(loadFinancialMonth).mockResolvedValue({
      ...result,
      month: "2026-11",
      days: [],
      movements: [],
    });
    render(<LiveFinancialCenter clientId="client_test" />);
    await screen.findByRole("heading", { name: "Cliente real" });
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("month")).toBe(
        "2026-11",
      ),
    );
    expect(loadFinancialMonth).toHaveBeenCalledTimes(1);
  });
  it("preserva o mês explícito mesmo sem movimentos", async () => {
    vi.mocked(loadFinancialMonth).mockResolvedValue({
      ...result,
      month: "2025-12",
      days: [],
      movements: [],
    });
    render(
      <LiveFinancialCenter
        clientId="client_test"
        initialQuery="month=2025-12"
      />,
    );
    await screen.findByRole("heading", { name: "Cliente real" });
    expect(vi.mocked(loadFinancialMonth).mock.calls[0][1]).toContain(
      "month=2025-12",
    );
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("month")).toBe(
        "2025-12",
      ),
    );
  });
  it("consulta um dia e abre a origem com retorno ao cliente e mês", async () => {
    render(
      <LiveFinancialCenter
        clientId="client_test"
        initialQuery="month=2026-04"
      />,
    );
    await screen.findByRole("heading", { name: "Cliente real" });
    fireEvent.click(screen.getByRole("button", { name: /02\/04\/2026/ }));
    await screen.findByText("Descrição revisada");
    expect(vi.mocked(loadFinancialMonth).mock.lastCall?.[1]).toContain("day=2");
    expect(
      screen.getByRole("link", { name: /Abrir origem/ }).getAttribute("href"),
    ).toContain("/review/batch_test/job_test?return_to=");
    expect(
      screen.getByRole("link", { name: /Abrir origem/ }).getAttribute("href"),
    ).toContain("month%3D2026-04");
    expect(
      screen.getAllByText(/Cobertura desconhecida/).length,
    ).toBeGreaterThan(0);
  });
  it("descarta dados ao falhar uma consulta e permite repetir", async () => {
    vi.mocked(loadFinancialMonth).mockRejectedValueOnce(
      new Error("Consulta indisponível"),
    );
    render(
      <LiveFinancialCenter
        clientId="client_test"
        initialQuery="month=2026-04"
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Consulta indisponível",
    );
    expect(screen.queryByText("Descrição revisada")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await screen.findByRole("heading", { name: "Cliente real" });
  });
  it("filtros por conta e mês consultam o servidor", async () => {
    render(
      <LiveFinancialCenter
        clientId="client_test"
        initialQuery="month=2026-04"
      />,
    );
    await screen.findByRole("heading", { name: "Cliente real" });
    fireEvent.change(screen.getByLabelText("Conta"), {
      target: { value: "account_test" },
    });
    await waitFor(() =>
      expect(vi.mocked(loadFinancialMonth).mock.lastCall?.[1]).toContain(
        "account=account_test",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Próximo mês" }));
    await waitFor(() =>
      expect(vi.mocked(loadFinancialMonth).mock.lastCall?.[1]).toContain(
        "month=2026-05",
      ),
    );
  });
});
