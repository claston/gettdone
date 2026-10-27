import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { StatementWorks } from "./statement-works";
import type {
  StatementWork,
  StatementWorkService,
} from "@/data/statement-work-service";
import type { OrganizationService } from "@/data/organization-service";

const client = { id: "c", name: "Aurora", tax_id: null, trade_name: "" };
const period = {
  id: "p",
  name: "Ciclo",
  start_date: "2026-09-20",
  end_date: "2026-10-19",
};
const account = {
  id: "a",
  name: "Principal",
  client_id: "c",
  bank_code: "033",
  branch: "1",
  account_number: "123",
};
function fixture() {
  const work: StatementWork = {
    id: "work_1",
    version: 1,
    state: "open",
    fingerprint: "a".repeat(64),
    client,
    period,
    accounts: [account],
    documents: [],
    settings: {
      account_ids: [],
      scope_confirmed_at: null,
      evidence: {},
      exclusions: {},
    },
    closings: [],
    events: [],
    needs_reassessment: false,
    assessment: {
      readiness: "blocked",
      can_complete: false,
      accounts_count: 0,
      documents_count: 0,
      movements_count: 0,
      movements: [],
      coverage: [],
      duplicate_groups: [],
      blockers: [
        {
          code: "scope_unconfirmed",
          message: "Confirme as contas esperadas",
          account_id: null,
          job_id: null,
        },
      ],
      reservations: [],
    },
  };
  const service = {
    get: vi.fn().mockResolvedValue(work),
    list: vi.fn().mockResolvedValue({ items: [], next_offset: null }),
    create: vi.fn().mockResolvedValue(work),
    scope: vi.fn().mockResolvedValue({
      ...work,
      version: 2,
      settings: {
        ...work.settings,
        account_ids: ["a"],
        scope_confirmed_at: "now",
      },
    }),
    attach: vi.fn(),
    attachBatch: vi.fn(),
    evidence: vi.fn(),
    exclude: vi.fn(),
    distinct: vi.fn(),
    complete: vi.fn(),
    reopen: vi.fn(),
  } satisfies StatementWorkService;
  const organization = {
    catalog: vi.fn().mockResolvedValue({
      clients: [client],
      periods: [period],
      accounts: [account],
    }),
    savePeriod: vi.fn(),
    saveAccount: vi.fn(),
  } as unknown as OrganizationService;
  return { work, service, organization };
}

it("exige confirmação explícita das contas e impede conclusão com bloqueios", async () => {
  const { work, service, organization } = fixture();
  render(
    <StatementWorks
      initialId="work_1"
      service={service}
      organization={organization}
    />,
  );
  expect(
    await screen.findByText("Confirme as contas esperadas"),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Contas e cobertura" }));
  expect(screen.getByRole("checkbox", { name: "Principal" })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "Principal" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar contas do trabalho" }),
  );
  await waitFor(() => expect(service.scope).toHaveBeenCalledWith(work, ["a"]));
  fireEvent.click(screen.getByRole("button", { name: "Resumo e exportação" }));
  expect(
    screen.getByRole("button", { name: "Concluir trabalho" }),
  ).toBeDisabled();
  expect(service.complete).not.toHaveBeenCalled();
});

it("reutiliza cliente único e período existente para criar trabalho sem upload", async () => {
  const { service, organization } = fixture();
  render(<StatementWorks service={service} organization={organization} />);
  const selector = await screen.findByLabelText("Cliente do trabalho");
  expect(selector).toHaveValue("c");
  fireEvent.change(screen.getByLabelText("Período do trabalho"), {
    target: { value: "p" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Criar ou abrir trabalho" }),
  );
  await waitFor(() => expect(service.create).toHaveBeenCalledWith("c", "p"));
});

it("mostra indisponibilidade sem impedir o acesso ao conversor", async () => {
  const { service, organization } = fixture();
  service.list.mockRejectedValue(new Error("Piloto indisponível"));
  render(<StatementWorks service={service} organization={organization} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Piloto indisponível",
  );
  expect(
    screen.getByRole("link", { name: "Ir para Conversões" }),
  ).toHaveAttribute("href", "/conversions");
});

it("preserva a versão concluída e oculta exportação quando exige reavaliação", async () => {
  const { work, service, organization } = fixture();
  work.state = "completed";
  work.needs_reassessment = true;
  work.closings = [
    {
      revision: 1,
      created_at: "2026-10-27T12:00:00Z",
      created_by: "owner",
      snapshot: {
        client,
        period,
        conclusion: "complete",
        remarks: {},
        assessment: work.assessment,
      },
    },
  ];
  service.get.mockResolvedValue(work);
  render(
    <StatementWorks
      initialId="work_1"
      service={service}
      organization={organization}
    />,
  );
  expect(
    await screen.findByText(/A versão concluída foi preservada/),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Resumo e exportação" }));
  expect(
    screen.queryByRole("link", { name: /Baixar movimentações/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/Versão 1/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Reabrir trabalho" }),
  ).toBeDisabled();
});
