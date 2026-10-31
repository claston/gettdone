import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NewConversionForm } from "./new-conversion-form";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

describe("Conversão PRO", () => {
  it("informa falha na consulta e permite consultar novamente sem iniciar conversão", async () => {
    const service = {
      createAndSubmit: vi.fn(),
      getProOptions: vi
        .fn()
        .mockRejectedValueOnce(new Error("404"))
        .mockResolvedValueOnce({
          enabled: true,
          eligible: true,
          remaining_pages: 20,
          monthly_pages: 20,
          max_pages: 6,
        }),
      preparePro: vi.fn(),
      submitPro: vi.fn(),
    };
    render(<NewConversionForm service={service} />);
    expect(
      await screen.findByText(
        "Não foi possível consultar a disponibilidade da Conversão PRO.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Consultar a Conversão PRO novamente",
      }),
    );
    expect(
      await screen.findByRole("radio", { name: "Conversão PRO" }),
    ).toBeEnabled();
    expect(
      screen.queryByText(
        "Não foi possível consultar a disponibilidade da Conversão PRO.",
      ),
    ).not.toBeInTheDocument();
    expect(service.getProOptions).toHaveBeenCalledTimes(2);
    expect(service.createAndSubmit).not.toHaveBeenCalled();
    expect(service.preparePro).not.toHaveBeenCalled();
    expect(service.submitPro).not.toHaveBeenCalled();
  });

  it("preserva a conversão padrão quando o servidor desabilita a opção PRO", async () => {
    const service = {
      createAndSubmit: vi
        .fn()
        .mockResolvedValue({ batchId: "standard-with-pro-off" }),
      getProOptions: vi.fn().mockResolvedValue({
        enabled: false,
        eligible: false,
        remaining_pages: 0,
        monthly_pages: 20,
        max_pages: 6,
      }),
    };
    render(<NewConversionForm service={service} />);
    fireEvent.change(screen.getByLabelText("Selecionar arquivos"), {
      target: {
        files: [new File(["data"], "extrato.csv", { type: "text/csv" })],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Criar lote" }));
    await waitFor(() =>
      expect(service.createAndSubmit).toHaveBeenCalledTimes(1),
    );
    expect(
      screen.queryByRole("radio", { name: "Conversão PRO" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "Não foi possível consultar a disponibilidade da Conversão PRO.",
      ),
    ).not.toBeInTheDocument();
  });

  it("confere as páginas e exige confirmação antes de iniciar a leitura paga", async () => {
    const service = {
      createAndSubmit: vi.fn(),
      getProOptions: vi.fn().mockResolvedValue({
        enabled: true,
        eligible: true,
        monthly_pages: 20,
        remaining_pages: 20,
        max_pages: 6,
      }),
      preparePro: vi.fn().mockResolvedValue({
        batchId: "pro-one",
        pages: 4,
        remaining_pages: 20,
        remaining_after: 16,
        monthly_pages: 20,
      }),
      submitPro: vi.fn().mockResolvedValue({ batchId: "pro-one" }),
    };
    render(<NewConversionForm service={service} />);
    fireEvent.click(
      await screen.findByRole("radio", { name: "Conversão PRO" }),
    );
    fireEvent.change(screen.getByLabelText("Selecionar arquivos"), {
      target: {
        files: [new File(["scan"], "extrato.pdf", { type: "application/pdf" })],
      },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Verificar Conversão PRO" }),
    );
    expect(
      await screen.findByText("4 páginas serão processadas."),
    ).toBeInTheDocument();
    expect(service.submitPro).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Iniciar Conversão PRO" }),
    );
    await waitFor(() =>
      expect(service.submitPro).toHaveBeenCalledWith("pro-one"),
    );
    expect(service.createAndSubmit).not.toHaveBeenCalled();
    await waitFor(() => expect(push).toHaveBeenCalledWith("/review/pro-one"));
  });

  it("mantém a opção bloqueada para uma conta sem assinatura paga", async () => {
    const service = {
      createAndSubmit: vi.fn(),
      getProOptions: vi.fn().mockResolvedValue({
        enabled: true,
        eligible: false,
        remaining_pages: 0,
        monthly_pages: 20,
        max_pages: 6,
      }),
    };
    render(<NewConversionForm service={service} />);
    expect(
      await screen.findByRole("radio", { name: "Conversão PRO" }),
    ).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Padrão" })).toBeChecked();
  });
});
