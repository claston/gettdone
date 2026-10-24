import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { OrganizationPage } from "./organization-page";
import type {
  OrganizationCatalog,
  OrganizationService,
} from "@/data/organization-service";

const catalog: OrganizationCatalog = {
  clients: [
    { id: "client-a", name: "Empresa A", tax_id: null, trade_name: "" },
  ],
  accounts: [],
  periods: [
    {
      id: "period-a",
      name: "Quinzena",
      start_date: "2026-09-20",
      end_date: "2026-10-04",
    },
  ],
};
function mockService(): OrganizationService {
  return {
    catalog: vi.fn().mockResolvedValue(catalog),
    listConversions: vi.fn().mockResolvedValue({
      items: [
        {
          job_id: "job-a",
          batch_id: "batch-a",
          filename: "extrato.pdf",
          status: "completed",
          is_expired: false,
          created_at: "2026-10-01T12:00:00Z",
          movement_start_date: null,
          movement_end_date: null,
          review_url: "/review/batch-a/job-a",
          context: {
            client_id: null,
            account_id: null,
            period_ids: [],
            version: 0,
          },
        },
      ],
      next_cursor: null,
    }),
    saveClient: vi.fn(),
    saveAccount: vi.fn(),
    savePeriod: vi.fn().mockResolvedValue(catalog.periods[0]),
    organize: vi.fn().mockResolvedValue({
      client_id: null,
      account_id: null,
      period_ids: ["period-a"],
      version: 1,
    }),
  };
}

it("mantém conversões sem cliente e combina cliente e período independentemente", async () => {
  const service = mockService();
  render(<OrganizationPage mode="clients" service={service} />);
  const file = await screen.findByRole("article", { name: "extrato.pdf" });
  expect(
    within(file).getByText("Sem cliente", { selector: "span" }),
  ).toBeInTheDocument();
  expect(within(file).getByText("Período a confirmar")).toBeInTheDocument();
  expect(
    within(file).getByRole("link", { name: "Abrir revisão" }),
  ).toHaveAttribute("href", "/review/batch-a/job-a");
  fireEvent.change(screen.getByLabelText("Filtrar por cliente"), {
    target: { value: "unassigned" },
  });
  fireEvent.change(screen.getByLabelText("Filtrar por período"), {
    target: { value: "period-a" },
  });
  await waitFor(() =>
    expect(service.listConversions).toHaveBeenLastCalledWith(
      { client: "unassigned", period: "period-a", account: "" },
      undefined,
      expect.any(AbortSignal),
    ),
  );
});

it("vincula período sem exigir cliente ou reprocessar o arquivo", async () => {
  const service = mockService();
  render(<OrganizationPage mode="conversions" service={service} />);
  const file = await screen.findByRole("article", { name: "extrato.pdf" });
  fireEvent.click(within(file).getByText("Organizar"));
  fireEvent.click(within(file).getByRole("checkbox", { name: /Quinzena/ }));
  fireEvent.click(
    within(file).getByRole("button", { name: "Salvar organização" }),
  );
  await waitFor(() =>
    expect(service.organize).toHaveBeenCalledWith("batch-a", "job-a", {
      client_id: null,
      account_id: null,
      period_ids: ["period-a"],
      version: 0,
    }),
  );
  expect(await screen.findByText("Organização salva.")).toBeInTheDocument();
});

it("cria um período que atravessa meses sem cadastro de cliente", async () => {
  const service = mockService();
  render(<OrganizationPage mode="periods" service={service} />);
  const form = await screen.findByRole("form", { name: "Cadastro de período" });
  fireEvent.change(within(form).getByLabelText("Nome do período"), {
    target: { value: "Fechamento especial" },
  });
  fireEvent.change(within(form).getByLabelText("Data inicial"), {
    target: { value: "2026-09-20" },
  });
  fireEvent.change(within(form).getByLabelText("Data final"), {
    target: { value: "2026-10-04" },
  });
  fireEvent.submit(form);
  await waitFor(() =>
    expect(service.savePeriod).toHaveBeenCalledWith(
      {
        name: "Fechamento especial",
        start_date: "2026-09-20",
        end_date: "2026-10-04",
      },
      undefined,
    ),
  );
});

it("preserva a seleção e mostra conflito quando o salvamento falha", async () => {
  const service = mockService();
  vi.mocked(service.organize).mockRejectedValue(
    new Error("Atualize a lista antes de salvar novamente."),
  );
  render(<OrganizationPage mode="conversions" service={service} />);
  const file = await screen.findByRole("article", { name: "extrato.pdf" });
  fireEvent.click(within(file).getByText("Organizar"));
  fireEvent.click(within(file).getByRole("checkbox", { name: /Quinzena/ }));
  fireEvent.click(
    within(file).getByRole("button", { name: "Salvar organização" }),
  );
  expect(
    await screen.findByText("Atualize a lista antes de salvar novamente."),
  ).toBeInTheDocument();
  expect(
    within(file).getByRole("checkbox", { name: /Quinzena/ }),
  ).toBeChecked();
  expect(screen.queryByText("Organização salva.")).not.toBeInTheDocument();
});

it("impede sobrescrever vínculos quando os cadastros não carregam", async () => {
  const service = mockService();
  vi.mocked(service.catalog).mockRejectedValue(
    new Error("Cadastros indisponíveis"),
  );
  render(<OrganizationPage mode="conversions" service={service} />);
  const file = await screen.findByRole("article", { name: "extrato.pdf" });
  fireEvent.click(within(file).getByText("Organizar"));
  expect(
    await screen.findByText("Cadastros indisponíveis"),
  ).toBeInTheDocument();
  expect(
    within(file).getByRole("button", { name: "Salvar organização" }),
  ).toBeDisabled();
  expect(service.organize).not.toHaveBeenCalled();
});
