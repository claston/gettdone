import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ReviewPanels } from "./review-panels";

it("ajusta a divisão por teclado, respeita limites e preserva os painéis ao recolher", () => {
  const { container } = render(
    <ReviewPanels>
      <section>PDF</section>
      <section>Lançamentos</section>
    </ReviewPanels>,
  );
  const divider = screen.getByRole("separator");
  fireEvent.keyDown(divider, { key: "ArrowRight" });
  expect(divider).toHaveAttribute("aria-valuenow", "50");
  fireEvent.keyDown(divider, { key: "End" });
  fireEvent.keyDown(divider, { key: "ArrowRight" });
  expect(divider).toHaveAttribute("aria-valuenow", "75");
  fireEvent.click(screen.getByRole("button", { name: "Recolher PDF" }));
  expect(container.firstChild).toHaveAttribute("data-collapsed", "pdf");
  expect(screen.getByText("PDF")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Mostrar PDF" }));
  expect(container.firstChild).not.toHaveAttribute("data-collapsed");
  fireEvent.doubleClick(divider);
  expect(divider).toHaveAttribute("aria-valuenow", "45");
});
