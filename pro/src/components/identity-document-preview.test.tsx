import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  loadReceivedDocument,
  type PendingStatement,
} from "@/data/received-statements-service";
import { IdentityDocumentPreview } from "./identity-document-preview";

vi.mock("@/data/received-statements-service", () => ({
  loadReceivedDocument: vi.fn(),
}));
vi.mock("next/dynamic", () => ({
  default: () =>
    function Viewer({ initialPage }: { initialPage: number }) {
      return <div>PDF na página {initialPage}</div>;
    },
}));
const document: PendingStatement = {
  job_id: "job",
  batch_id: "batch",
  filename: "extrato.PDF",
  version: 0,
  source: {
    references: [
      { page: 0, line: 1 },
      { page: 2, line: 3 },
    ],
  },
};
beforeEach(() => vi.mocked(loadReceivedDocument).mockReset());

describe("prévia para identificação", () => {
  it("abre a página de evidência e cancela a leitura ao fechar", async () => {
    vi.mocked(loadReceivedDocument).mockResolvedValue(new Blob(["pdf"]));
    const { unmount } = render(<IdentityDocumentPreview document={document} />);
    await screen.findByText("PDF na página 2");
    const [batch, job, signal] = vi.mocked(loadReceivedDocument).mock.calls[0];
    expect([batch, job]).toEqual(["batch", "job"]);
    expect(signal.aborted).toBe(false);
    unmount();
    expect(signal.aborted).toBe(true);
  });
  it("mantém a falha explícita e permite recarregar", async () => {
    vi.mocked(loadReceivedDocument)
      .mockRejectedValueOnce(new Error("PDF indisponível ou expirado."))
      .mockResolvedValue(new Blob(["pdf"]));
    render(<IdentityDocumentPreview document={{ ...document, source: {} }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "PDF indisponível ou expirado.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Recarregar PDF" }));
    await screen.findByText("PDF na página 1");
    expect(loadReceivedDocument).toHaveBeenCalledTimes(2);
  });
  it("não busca PDF para arquivos de outros formatos", () => {
    render(
      <IdentityDocumentPreview
        document={{ ...document, filename: "extrato.csv" }}
      />,
    );
    expect(screen.getByText(/apenas para arquivos PDF/)).toBeVisible();
    expect(loadReceivedDocument).not.toHaveBeenCalled();
  });
  it("descarta uma resposta atrasada ao trocar o documento", async () => {
    let finish!: (blob: Blob) => void;
    vi.mocked(loadReceivedDocument)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      )
      .mockResolvedValue(new Blob(["second"]));
    const { rerender } = render(
      <IdentityDocumentPreview document={document} />,
    );
    const signal = vi.mocked(loadReceivedDocument).mock.calls[0][2];
    rerender(
      <IdentityDocumentPreview
        document={{ ...document, job_id: "other", source: {} }}
      />,
    );
    await screen.findByText("PDF na página 1");
    expect(signal.aborted).toBe(true);
    await act(async () => finish(new Blob(["first"])));
    await waitFor(() =>
      expect(screen.queryByText("PDF na página 2")).toBeNull(),
    );
  });
});
