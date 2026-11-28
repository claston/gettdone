import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MonthFilter } from "./month-filter";

function setup(month = "2026-07") {
  const onChange = vi.fn();
  render(
    <MonthFilter
      month={month}
      onChange={onChange}
      allHref="/dashboard"
      unknownHref="/dashboard?month=unknown"
    />,
  );
  return onChange;
}

describe("Filtro de mês", () => {
  it("mostra o mês por extenso e aplica a escolha sem alterar o ano", () => {
    const change = setup();
    const trigger = screen.getByRole("button", { name: "Mês de trabalho" });
    expect(trigger).toHaveTextContent("Julho de 2026");
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Escolher mês" });
    expect(within(dialog).getByRole("button", { name: "Julho" })).toHaveFocus();
    fireEvent.click(within(dialog).getByRole("button", { name: "Agosto" }));
    expect(change).toHaveBeenCalledExactlyOnceWith("2026-08");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("permite navegar entre anos e digitar um ano distante sem navegar antes da escolha", () => {
    const change = setup("2026-12");
    fireEvent.click(screen.getByLabelText("Mês de trabalho"));
    fireEvent.click(screen.getByRole("button", { name: "Próximo ano" }));
    expect(screen.getByLabelText("Ano")).toHaveValue(2027);
    fireEvent.click(screen.getByRole("button", { name: "Ano anterior" }));
    expect(screen.getByLabelText("Ano")).toHaveValue(2026);
    fireEvent.change(screen.getByLabelText("Ano"), {
      target: { value: "2001" },
    });
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Janeiro" }));
    expect(change).toHaveBeenCalledWith("2001-01");
  });

  it("não aplica um mês enquanto o ano está vazio ou inválido", () => {
    const change = setup();
    fireEvent.click(screen.getByLabelText("Mês de trabalho"));
    for (const value of ["", "0", "10000"]) {
      fireEvent.change(screen.getByLabelText("Ano"), { target: { value } });
      expect(screen.getByRole("button", { name: "Janeiro" })).toBeDisabled();
    }
    expect(change).not.toHaveBeenCalled();
  });

  it("fecha com Escape ou clique externo sem aplicar alterações", () => {
    const change = setup();
    const trigger = screen.getByLabelText("Mês de trabalho");
    fireEvent.click(trigger);
    fireEvent.keyDown(screen.getByLabelText("Ano"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(change).not.toHaveBeenCalled();
  });

  it.each([
    ["", "Todos os meses"],
    ["unknown", "Período a confirmar"],
  ])(
    "mostra o filtro ativo para %s sem selecionar um mês arbitrário",
    (month, name) => {
      setup(month);
      expect(screen.getByLabelText("Mês de trabalho")).toHaveTextContent(
        "Selecionar mês",
      );
      expect(screen.getByRole("link", { name })).toHaveAttribute(
        "aria-current",
        "page",
      );
    },
  );
});
