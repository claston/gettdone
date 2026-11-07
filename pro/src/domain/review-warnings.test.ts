import { expect, it } from "vitest";
import { reviewWarningText } from "./review-warnings";

it("explica referência compartilhada sem sugerir que o valor está errado", () => {
  const message = reviewWarningText([
    "manual_review_recommended",
    "shared_operation_reference",
  ]);
  expect(message).toContain("não comprova duplicidade");
  expect(message).toContain("preservados");
});

it("prioriza divergência real mesmo com referência compartilhada", () => {
  expect(
    reviewWarningText([
      "shared_operation_reference",
      "balance_consistency_failed",
    ]),
  ).toContain("Saldo não confere");
});
