import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { OfficeDocumentControls, OfficePackage } from "./office-controls";
import { officeService, saveOfficeFile } from "@/data/office-service";
import type { DocumentItem } from "@/data/documents-service";

vi.mock("@/data/office-service", () => ({
  officeService: { verify: vi.fn(), preview: vi.fn(), download: vi.fn() },
  saveOfficeFile: vi.fn(),
}));
vi.mock("../identity-document-preview", () => ({
  AuthorizedIdentityPdf: () => <p>PDF autorizado</p>,
}));
beforeEach(() => vi.resetAllMocks());

it("shows explicit exclusions and requires acknowledgement for partial delivery", async () => {
  vi.mocked(officeService.preview).mockResolvedValue({
    snapshot: "snapshot",
    items: [
      { job_id: "a", filename: "extrato.pdf", included: true, reason: null },
      { job_id: "b", included: false, reason: "Prazo de acesso encerrado" },
    ],
  });
  vi.mocked(officeService.download).mockResolvedValue(new Blob(["zip"]));
  render(<OfficePackage clientId="client" month="2026-07" jobs={["a", "b"]} />);
  fireEvent.click(screen.getByRole("button", { name: "Conferir pacote (2)" }));
  expect(await screen.findByText(/Prazo de acesso encerrado/)).toBeVisible();
  const download = screen.getByRole("button", { name: "Baixar pacote ZIP" });
  expect(download).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(download);
  await waitFor(() =>
    expect(officeService.download).toHaveBeenCalledWith(
      { client_id: "client", month: "2026-07", job_ids: ["a", "b"] },
      "snapshot",
      true,
    ),
  );
  await waitFor(() => expect(saveOfficeFile).toHaveBeenCalled());
});

it("invalidates the preview when the server reports concurrent changes", async () => {
  vi.mocked(officeService.preview).mockResolvedValue({
    snapshot: "old",
    items: [{ job_id: "a", included: true, reason: null }],
  });
  vi.mocked(officeService.download).mockRejectedValue(
    new Error("A seleção mudou. Confira novamente a prévia do pacote."),
  );
  render(<OfficePackage clientId="client" month="" jobs={["a"]} />);
  fireEvent.click(screen.getByRole("button", { name: "Conferir pacote (1)" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Baixar pacote ZIP" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("A seleção mudou");
  expect(
    screen.queryByRole("button", { name: "Baixar pacote ZIP" }),
  ).toBeNull();
  expect(saveOfficeFile).not.toHaveBeenCalled();
});

it("verifies a support document with source/version and keeps dates unknown", async () => {
  const item = {
    job_id: "a",
    batch_id: "batch",
    filename: "nota.pdf",
    source: "source",
    is_bank: false,
    verification: {
      status: "pending",
      version: 2,
      document_type: null,
      actor: null,
      updated_at: null,
    },
  } as DocumentItem;
  const onSaved = vi.fn();
  render(<OfficeDocumentControls item={item} onSaved={onSaved} />);
  expect(screen.getByText("Período a confirmar")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Ver PDF original" }));
  expect(screen.getByText("PDF autorizado")).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "Marcar como verificado" }),
  );
  await waitFor(() =>
    expect(officeService.verify).toHaveBeenCalledWith(
      "a",
      expect.objectContaining({
        source: "source",
        expected_version: 2,
        status: "verified",
      }),
    ),
  );
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
});
