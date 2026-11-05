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

vi.mock("@/data/financial-center-service", async (original) => ({
  ...(await original<typeof import("@/data/financial-center-service")>()),
  loadFinancialMonth: vi.fn(),
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
  window.history.replaceState(null, "", "/");
});
describe("Central canônica", () => {
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
