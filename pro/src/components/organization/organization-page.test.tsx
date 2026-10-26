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
    suggestions: vi
      .fn()
      .mockResolvedValue({ clients: [], accounts: [], period: null }),
    organizeBulk: vi.fn(),
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

it("mantém arquivos e revisão acessíveis quando organização está indisponível", async () => {
  const service = mockService();
  vi.mocked(service.catalog).mockRejectedValue(
    new Error("Organização indisponível"),
  );
  render(<OrganizationPage mode="conversions" service={service} independent />);
  const file = await screen.findByRole("article", { name: "extrato.pdf" });
  expect(
    within(file).getByRole("link", { name: "Abrir revisão" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByLabelText("Filtrar por cliente"),
  ).not.toBeInTheDocument();
  expect(
    within(file).queryByRole("button", { name: "Organizar" }),
  ).not.toBeInTheDocument();
  expect(service.listConversions).toHaveBeenCalledWith(
    { client: "", account: "", period: "", organization: false },
    undefined,
    expect.any(AbortSignal),
  );
});

it("organiza seleção sem apagar períodos existentes e informa conflitos por arquivo", async () => {
  const service = mockService();
  vi.mocked(service.organizeBulk).mockResolvedValue({
    items: [
      { job_id: "job-a", status: 409, error: "Alterado por outra sessão" },
    ],
  });
  render(<OrganizationPage mode="conversions" service={service} />);
  fireEvent.click(await screen.findByLabelText("Selecionar extrato.pdf"));
  fireEvent.change(screen.getByLabelText("Período para os selecionados"), {
    target: { value: "period-a" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Aplicar organização" }));
  expect(
    await screen.findByText(/extrato.pdf: Alterado por outra sessão/),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Selecionar extrato.pdf")).toBeChecked();
  expect(service.organizeBulk).toHaveBeenCalledWith([
    expect.objectContaining({
      job_id: "job-a",
      context: expect.objectContaining({
        period_ids: ["period-a"],
        version: 0,
      }),
    }),
  ]);
});

it("mostra evidência e só confirma sugestão ao salvar", async () => {
  const service = mockService();
  vi.mocked(service.suggestions).mockResolvedValue({
    clients: [
      {
        id: "client-a",
        basis: "holder_tax_id",
        confidence: "strong",
        evidence: "CPF/CNPJ do titular: 12345678000190",
      },
    ],
    accounts: [],
    period: null,
  });
  render(<OrganizationPage mode="conversions" service={service} />);
  const file = await screen.findByRole("article", { name: "extrato.pdf" });
  fireEvent.click(within(file).getByRole("button", { name: "Organizar" }));
  fireEvent.click(
    within(file).getByRole("button", { name: "Buscar sugestões" }),
  );
  expect(
    await screen.findByText("CPF/CNPJ do titular: 12345678000190"),
  ).toBeInTheDocument();
  expect(service.organize).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Usar cliente Empresa A" }),
  );
  expect(within(file).getByLabelText("Cliente do arquivo")).toHaveValue(
    "client-a",
  );
  expect(service.organize).not.toHaveBeenCalled();
  fireEvent.click(
    within(file).getByRole("button", { name: "Salvar organização" }),
  );
  await waitFor(() =>
    expect(service.organize).toHaveBeenCalledWith(
      "batch-a",
      "job-a",
      expect.objectContaining({ client_id: "client-a" }),
    ),
  );
});

it("mantém conversões sem cliente e combina cliente e período independentemente", async () => {
  const service = mockService();
  render(<OrganizationPage mode="clients" service={service} />);
  const file = await screen.findByRole("article", { name: "extrato.pdf" });
  expect(
    within(file).getByText("Sem cliente", { selector: "span" }),
  ).toBeInTheDocument();
  expect(
    within(file).getByText("Período de trabalho não definido"),
  ).toBeInTheDocument();
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
