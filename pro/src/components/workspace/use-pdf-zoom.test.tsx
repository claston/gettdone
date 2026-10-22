import { act, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { describe, expect, it } from "vitest";
import { usePdfZoom } from "./use-pdf-zoom";

function Viewer() {
  const stage = useRef<HTMLDivElement>(null);
  const { zoom, increase, decrease, reset } = usePdfZoom(stage, true);
  return (
    <>
      <input aria-label="Descrição da transação" />
      <section className="pdf-panel">
        <input aria-label="Buscar no PDF" />
        <button onClick={increase}>Ampliar</button>
        <button onClick={decrease}>Reduzir</button>
        <button onClick={reset}>Ajustar</button>
        <output aria-label="Escala">{zoom}</output>
        <div ref={stage} tabIndex={0} role="region" aria-label="PDF" />
      </section>
    </>
  );
}

function key(target: HTMLElement | Window, key: string, modifiers = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...modifiers,
  });
  act(() => target.dispatchEvent(event));
  return event;
}

describe("zoom local do PDF", () => {
  it("intercepta Ctrl/Cmd + +, − e 0 só dentro do visualizador e compartilha a escala com os botões", () => {
    render(<Viewer />);
    const stage = screen.getByRole("region", { name: "PDF" });
    stage.focus();
    expect(key(stage, "=", { ctrlKey: true }).defaultPrevented).toBe(true);
    expect(screen.getByLabelText("Escala")).toHaveTextContent("1.1");
    key(stage, "+", { metaKey: true });
    expect(screen.getByLabelText("Escala")).toHaveTextContent("1.2");
    key(stage, "-", { ctrlKey: true });
    expect(screen.getByLabelText("Escala")).toHaveTextContent("1.1");
    fireEvent.click(screen.getByText("Ampliar"));
    expect(screen.getByLabelText("Escala")).toHaveTextContent("1.2");
    key(stage, "0", { ctrlKey: true });
    expect(screen.getByLabelText("Escala")).toHaveTextContent("1");
  });

  it("preserva a rolagem normal e impede o zoom do navegador com Ctrl + roda ou pinça do trackpad", () => {
    render(<Viewer />);
    const stage = screen.getByRole("region", { name: "PDF" });
    const wheel = (ctrlKey: boolean, deltaY: number) => {
      const event = new WheelEvent("wheel", {
        ctrlKey,
        deltaY,
        bubbles: true,
        cancelable: true,
      });
      act(() => stage.dispatchEvent(event));
      return event;
    };
    expect(wheel(false, -100).defaultPrevented).toBe(false);
    expect(screen.getByLabelText("Escala")).toHaveTextContent("1");
    expect(wheel(true, -100).defaultPrevented).toBe(true);
    expect(Number(screen.getByLabelText("Escala").textContent)).toBeGreaterThan(
      1,
    );
    expect(wheel(true, 0.2).defaultPrevented).toBe(true);
    for (let i = 0; i < 30; i++) wheel(true, -100);
    expect(Number(screen.getByLabelText("Escala").textContent)).toBe(2);
    for (let i = 0; i < 30; i++) wheel(true, 100);
    expect(Number(screen.getByLabelText("Escala").textContent)).toBe(0.5);
  });

  it("usa o hover para os atalhos modificados e respeita campos de edição e a área fora do PDF", () => {
    render(<Viewer />);
    const panel = screen
      .getByRole("region", { name: "PDF" })
      .closest("section")!;
    expect(key(window, "=", { ctrlKey: true }).defaultPrevented).toBe(false);
    fireEvent.mouseEnter(panel);
    expect(key(window, "+", { ctrlKey: true }).defaultPrevented).toBe(true);
    for (const name of ["Buscar no PDF", "Descrição da transação"]) {
      const input = screen.getByLabelText(name);
      input.focus();
      expect(key(input, "+", { ctrlKey: true }).defaultPrevented).toBe(false);
      expect(key(input, "+").defaultPrevented).toBe(false);
    }
    fireEvent.mouseLeave(panel);
    screen.getByLabelText("Descrição da transação").blur();
    expect(key(window, "-", { ctrlKey: true }).defaultPrevented).toBe(false);
  });
});
