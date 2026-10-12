import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NewConversionForm } from "./new-conversion-form";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

describe("NewConversionForm", () => {
  beforeEach(() => push.mockReset());

  it("envia os arquivos e abre o lote real retornado pelo backend", async () => {
    const service = {
      createAndSubmit: vi.fn().mockResolvedValue({ batchId: "batch-real" }),
    };
    render(<NewConversionForm service={service} />);
    const file = new File(["conteúdo"], "extrato.pdf", {
      type: "application/pdf",
    });

    fireEvent.change(screen.getByLabelText("Selecionar arquivos"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Criar lote" }));

    await waitFor(() =>
      expect(service.createAndSubmit).toHaveBeenCalledWith([file]),
    );
    expect(push).toHaveBeenCalledWith("/review/batch-real");
  });

  it("explica quando o worker não está disponível", async () => {
    const service = {
      createAndSubmit: vi
        .fn()
        .mockRejectedValue(
          new Error("O processamento assíncrono está indisponível."),
        ),
    };
    render(<NewConversionForm service={service} />);

    fireEvent.change(screen.getByLabelText("Selecionar arquivos"), {
      target: {
        files: [
          new File(["conteúdo"], "extrato.pdf", {
            type: "application/pdf",
          }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Criar lote" }));

    expect(
      await screen.findByText("O processamento assíncrono está indisponível."),
    ).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
