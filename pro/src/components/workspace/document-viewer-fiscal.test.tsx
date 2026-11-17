import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import { DocumentViewer } from "./document-viewer";

vi.mock("./real-document-viewer", () => ({
  RealDocumentViewer: () => <div role="document">PDF original</div>,
}));

it.each(["fiscal_invoice", "UnsupportedDocumentContentError", "InternalError"])(
  "encaminha a prévia de %s sem marcar a conversão concluída",
  async (code) => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const batch = await repository.getBatch("batch-setembro-2026");
    const conversion = batch.conversions[0];
    Object.assign(conversion, {
      status: "FAILED",
      errorCode: code,
      errorMessage:
        "Este arquivo é uma nota fiscal eletrônica (NF-e ou NFS-e).",
      pdf: { url: "/original.pdf", fileName: "nota.pdf", pageCount: 1 },
    });
    render(
      <DocumentViewer
        conversion={conversion}
        repository={repository}
        page={1}
        setPage={vi.fn()}
        navigation={null}
        onAvailability={vi.fn()}
      />,
    );
    if (code === "InternalError")
      expect(
        screen.getByText("Não foi possível processar o arquivo"),
      ).toBeVisible();
    else expect(screen.getByRole("document")).toHaveTextContent("PDF original");
  },
);
