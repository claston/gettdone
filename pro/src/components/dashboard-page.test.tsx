import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./dashboard-page";
import type {
  BatchHistoryItem,
  BatchHistoryService,
} from "@/data/batch-history-service";

const item: BatchHistoryItem = {
  id: "batch-own",
  status: "completed",
  createdAt: "2026-10-17T12:00:00Z",
  expiresAt: "2026-10-18T12:00:00Z",
  isExpired: false,
  fileCount: 5,
  completedCount: 5,
  failedCount: 0,
  pendingCount: 0,
  reviewUrl: "/review/batch-own/job-own",
};

describe("histórico de lotes no dashboard", () => {
  it("mostra o histórico já carregado enquanto o servidor atualiza em segundo plano", async () => {
    const cached = { items: [item], nextCursor: null };
    const service: BatchHistoryService = {
      getCachedPage: () => cached,
      list: vi.fn(() => new Promise<typeof cached>(() => {})),
    };
    render(<DashboardPage service={service} />);
    expect(
      screen.getByRole("link", { name: "Abrir lote" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Carregando lotes…")).not.toBeInTheDocument();
  });
  it("lista lotes reais com reabertura, paginação e expirados sem link", async () => {
    const service: BatchHistoryService = {
      list: vi
        .fn()
        .mockResolvedValueOnce({ items: [item], nextCursor: "next-page" })
        .mockResolvedValueOnce({
          items: [
            {
              ...item,
              id: "expired",
              status: "expired",
              isExpired: true,
              reviewUrl: null,
            },
          ],
          nextCursor: null,
        })
        .mockResolvedValueOnce({ items: [item], nextCursor: "next-page" }),
    };
    render(<DashboardPage service={service} />);
    expect(
      await screen.findByRole("link", { name: "Abrir lote" }),
    ).toHaveAttribute("href", item.reviewUrl);
    fireEvent.click(screen.getByRole("button", { name: "Próxima" }));
    expect(await screen.findByText("Expirado")).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Abrir lote" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Indisponível")).toBeInTheDocument();
    expect(service.list).toHaveBeenNthCalledWith(
      2,
      "next-page",
      expect.any(AbortSignal),
    );
    fireEvent.click(screen.getByRole("button", { name: "Anterior" }));
    expect(
      await screen.findByRole("link", { name: "Abrir lote" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anterior" })).toBeDisabled();
  });

  it("mostra falha com nova tentativa sem inserir métricas de demonstração", async () => {
    const service: BatchHistoryService = {
      list: vi
        .fn()
        .mockRejectedValueOnce(new Error("Falha temporária"))
        .mockResolvedValueOnce({ items: [], nextCursor: null }),
    };
    render(<DashboardPage service={service} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Falha temporária",
    );
    fireEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    await waitFor(() =>
      expect(
        screen.getByText("Nenhum lote de conversão encontrado."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
