import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { DocumentConference } from "./document-conference";
import { loadFinancialMonth } from "@/data/financial-center-service";
import { conferenceService } from "@/data/document-conference-service";
import { documentsService } from "@/data/documents-service";

vi.mock("@/data/financial-center-service", async (original) => ({
  ...(await original<object>()),
  loadFinancialMonth: vi.fn(),
}));
vi.mock("@/data/document-conference-service", () => ({
  conferenceService: {
    document: vi.fn(),
    links: vi.fn(),
    confirm: vi.fn(),
    undo: vi.fn(),
  },
}));
vi.mock("@/data/documents-service", () => ({
  documentsService: { list: vi.fn() },
}));
vi.mock("../identity-document-preview", () => ({
  AuthorizedIdentityPdf: ({
    jobId,
    initialPage,
  }: {
    jobId: string;
    initialPage: number;
  }) => (
    <p>
      Original {jobId}, página {initialPage}
    </p>
  ),
}));
const movement = {
  id: "mv1",
  date: "2026-07-20",
  amount: "-4189.59",
  description: "PIX FORNECEDOR EXEMPLO",
  currency: "BRL",
  version: 3,
  account_id: "account",
  review_state: "pending",
  pending: true,
  possible_duplicate: false,
  source: {
    job_id: "bank",
    batch_id: "batch",
    filename: "extrato.pdf",
    page: 2,
  },
};
const evidence = {
  job_id: "receipt",
  batch_id: "batch",
  filename: "comprovante.pdf",
  document_type: "bank_receipt",
  source: "hash",
  expires_at: "2099-01-01T00:00:00Z",
  observations: [],
  notice: "Confira o original.",
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(loadFinancialMonth).mockResolvedValue({
    client: { id: "client", name: "Clínica" },
    month: "2026-07",
    accounts: [],
    movements: [
      movement,
      { ...movement, id: "mv2", description: "PIX OUTRO DESTINATÁRIO" },
    ],
    next_cursor: null,
    features: { document_conference: true },
    access_expires_at: "2099-01-01T00:00:00Z",
    legacy_sources: 0,
    unavailable_sources: 0,
  } as never);
  vi.mocked(documentsService.list).mockResolvedValue({
    items: [
      {
        ...evidence,
        category: "receipts",
        status: "completed",
        context: { client_id: "client" },
        is_expired: false,
      },
    ],
    next_cursor: null,
  } as never);
  vi.mocked(conferenceService.links).mockResolvedValue({
    links: [],
    unavailable_links: 0,
    partial: false,
    access_expires_at: "2099-01-01T00:00:00Z",
  });
  vi.mocked(conferenceService.document).mockResolvedValue(evidence);
  vi.mocked(conferenceService.confirm).mockResolvedValue({
    id: "link",
    version: 1,
  });
});

it("compares originals and requires an explicit acknowledgement before saving a documentary link", async () => {
  render(
    <DocumentConference
      clientId="client"
      month="2026-07"
      accountId="account"
      returnTo="/documents?client=client"
    />,
  );
  await screen.findByRole("button", { name: /PIX FORNECEDOR EXEMPLO/ });
  await screen.findByRole("option", { name: "comprovante.pdf" });
  fireEvent.change(screen.getByLabelText("Documento para comparar"), {
    target: { value: "receipt" },
  });
  const confirm = await screen.findByRole("button", {
    name: "Confirmar relação documental",
  });
  expect(confirm).toBeDisabled();
  expect(screen.getByText(/outra movimentação de mesmo valor/)).toBeVisible();
  expect(conferenceService.confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Ver extrato original" }));
  expect(await screen.findByText("Original bank, página 2")).toBeVisible();
  fireEvent.click(screen.getByLabelText(/Conferi os documentos/));
  fireEvent.click(confirm);
  await waitFor(() =>
    expect(conferenceService.confirm).toHaveBeenCalledWith(
      "client",
      "mv1",
      expect.objectContaining({
        job_id: "receipt",
        source: "hash",
        movement_version: 3,
      }),
      expect.any(AbortSignal),
    ),
  );
  expect(screen.getByText(/não confirma quitação fiscal/i)).toBeVisible();
});

it("does not carry confirmation to a different movement", async () => {
  render(
    <DocumentConference
      clientId="client"
      month="2026-07"
      accountId=""
      returnTo="/documents"
    />,
  );
  await screen.findByRole("button", { name: /PIX FORNECEDOR EXEMPLO/ });
  await screen.findByRole("option", { name: "comprovante.pdf" });
  fireEvent.change(screen.getByLabelText("Documento para comparar"), {
    target: { value: "receipt" },
  });
  fireEvent.click(await screen.findByLabelText(/Conferi os documentos/));
  fireEvent.click(
    screen.getByRole("button", { name: /PIX OUTRO DESTINATÁRIO/ }),
  );
  await screen.findByRole("option", { name: "comprovante.pdf" });
  fireEvent.change(screen.getByLabelText("Documento para comparar"), {
    target: { value: "receipt" },
  });
  expect(
    await screen.findByRole("button", { name: "Confirmar relação documental" }),
  ).toBeDisabled();
  expect(conferenceService.confirm).not.toHaveBeenCalled();
});

it("keeps incomplete coverage visible and marks changed links for review", async () => {
  vi.mocked(conferenceService.links).mockResolvedValue({
    links: [
      { id: "link", version: 2, state: "review_required", document: evidence },
    ],
    unavailable_links: 1,
    partial: false,
    access_expires_at: "2099-01-01T00:00:00Z",
  });
  render(
    <DocumentConference
      clientId="client"
      month="2026-07"
      accountId=""
      returnTo="/documents"
    />,
  );
  expect(await screen.findByText(/Origem alterada/)).toBeVisible();
  expect(
    screen.getByText(/documentos relacionados não estão disponíveis/),
  ).toBeVisible();
  expect(screen.getByText(/não comprova a cobertura completa/)).toBeVisible();
});

it("does not call documentary APIs against a backend without the capability", async () => {
  const month = await loadFinancialMonth(
    "client",
    "",
    new AbortController().signal,
  );
  vi.mocked(loadFinancialMonth).mockResolvedValue({
    ...month,
    features: undefined,
  });
  render(
    <DocumentConference
      clientId="client"
      month="2026-07"
      accountId=""
      returnTo="/documents"
    />,
  );
  expect(
    await screen.findByText(/vínculos documentais ainda não está disponível/),
  ).toBeVisible();
  expect(conferenceService.links).not.toHaveBeenCalled();
  expect(conferenceService.document).not.toHaveBeenCalled();
  expect(
    screen.getByRole("link", { name: "Revisar lançamento" }),
  ).toBeVisible();
});

it("hides an expired financial snapshot and offers an explicit refresh", async () => {
  const month = await loadFinancialMonth(
    "client",
    "",
    new AbortController().signal,
  );
  vi.mocked(loadFinancialMonth).mockResolvedValue({
    ...month,
    access_expires_at: "2000-01-01T00:00:00Z",
  });
  render(
    <DocumentConference
      clientId="client"
      month="2026-07"
      accountId=""
      returnTo="/documents"
    />,
  );
  expect(
    await screen.findByText(/O prazo desta consulta terminou/),
  ).toBeVisible();
  expect(screen.queryByText(movement.description)).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Atualizar conferência" }),
  ).toBeEnabled();
  expect(conferenceService.links).not.toHaveBeenCalled();
});

it("opens the reviewer for a non-PDF evidence document", async () => {
  vi.mocked(conferenceService.document).mockResolvedValue({
    ...evidence,
    filename: "nota.xml",
  });
  render(
    <DocumentConference
      clientId="client"
      month="2026-07"
      accountId=""
      returnTo="/documents"
    />,
  );
  await screen.findByRole("option", { name: "comprovante.pdf" });
  fireEvent.change(screen.getByLabelText("Documento para comparar"), {
    target: { value: "receipt" },
  });
  expect(
    await screen.findByRole("link", { name: "Abrir revisão do documento" }),
  ).toHaveAttribute("href", "/review/batch/receipt?return_to=%2Fdocuments");
  expect(
    screen.queryByRole("button", { name: "Ver documento original" }),
  ).not.toBeInTheDocument();
});
