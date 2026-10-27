import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NewConversionForm } from "./new-conversion-form";
import type { StatementWorkService } from "@/data/statement-work-service";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

describe("NewConversionForm", () => {
  it("retoma o vínculo do trabalho sem criar ou cobrar um segundo lote", async () => {
    const service = {
      createAndSubmit: vi.fn().mockResolvedValue({ batchId: "batch-once" }),
    };
    const workService = {
      get: vi
        .fn()
        .mockResolvedValue({ id: "work_1", state: "open", version: 1 }),
      attachBatch: vi
        .fn()
        .mockRejectedValueOnce(new Error("Vínculo indisponível"))
        .mockResolvedValueOnce({}),
    } as unknown as StatementWorkService;
    render(
      <NewConversionForm
        service={service}
        workId="work_1"
        workService={workService}
      />,
    );
    fireEvent.change(screen.getByLabelText("Selecionar arquivos"), {
      target: { files: [new File(["a"], "extrato.csv", { type: "text/csv" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Criar lote" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Vínculo indisponível",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Retomar vínculo com o trabalho" }),
    );
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        "/review/batch-once?return_to=%2Fworks%2Fwork_1",
      ),
    );
    expect(service.createAndSubmit).toHaveBeenCalledTimes(1);
    expect(workService.attachBatch).toHaveBeenCalledTimes(2);
  });
  beforeEach(() => push.mockReset());

  it("permite retirar só um arquivo de um lote com 13 e enviar os 12 restantes", async () => {
    const files = Array.from(
      { length: 13 },
      (_, index) =>
        new File([`conteúdo ${index}`], `extrato-${index + 1}.pdf`, {
          type: "application/pdf",
        }),
    );
    const service = {
      createAndSubmit: vi
        .fn()
        .mockRejectedValueOnce(new Error("Selecione entre 1 e 12 arquivos."))
        .mockResolvedValueOnce({ batchId: "batch-twelve" }),
    };
    render(<NewConversionForm service={service} />);
    fireEvent.change(screen.getByLabelText("Selecionar arquivos"), {
      target: { files },
    });
    fireEvent.click(screen.getByRole("button", { name: "Criar lote" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Selecione entre 1 e 12 arquivos.",
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Remover extrato-7.pdf" }),
    );
    expect(screen.queryByText("extrato-7.pdf")).not.toBeInTheDocument();
    expect(screen.getByText("12 arquivos selecionados")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Criar lote" }));

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/review/batch-twelve"),
    );
    expect(service.createAndSubmit).toHaveBeenLastCalledWith(
      files.filter((_, index) => index !== 6),
    );
  });

  it("desabilita a remoção durante o envio do lote", async () => {
    let finish!: (result: { batchId: string }) => void;
    const service = {
      createAndSubmit: vi.fn(
        () =>
          new Promise<{ batchId: string }>((resolve) => {
            finish = resolve;
          }),
      ),
    };
    render(<NewConversionForm service={service} />);
    fireEvent.change(screen.getByLabelText("Selecionar arquivos"), {
      target: { files: [new File(["a"], "extrato.pdf")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Criar lote" }));
    expect(
      screen.getByRole("button", { name: "Remover extrato.pdf" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Selecionar arquivos")).toBeDisabled();
    finish({ batchId: "batch-uploading" });
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/review/batch-uploading"),
    );
  });

  it("remove só a ocorrência escolhida e permite selecionar o mesmo arquivo novamente", () => {
    const file = new File(["conteúdo"], "extrato.pdf");
    render(<NewConversionForm service={{ createAndSubmit: vi.fn() }} />);
    const input = screen.getByLabelText("Selecionar arquivos");
    fireEvent.change(input, { target: { files: [file, file] } });
    fireEvent.click(
      screen.getAllByRole("button", { name: "Remover extrato.pdf" })[0],
    );
    expect(screen.getByText("1 arquivo selecionado")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Remover extrato.pdf" }),
    );
    expect(
      screen.queryByRole("list", { name: "Arquivos selecionados" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Criar lote" })).toBeDisabled();
    fireEvent.change(input, { target: { files: [file] } });
    expect(
      screen.getByRole("button", { name: "Remover extrato.pdf" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Criar lote" })).toBeEnabled();
  });

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
