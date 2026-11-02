import { expect, it } from "vitest";
import { reviewReturnTo } from "./review-return";

it("preserva filtros de conversões e rejeita destinos externos", () => {
  expect(
    reviewReturnTo(
      "/demo/financial/multiple?month=2026-09&accounts=itau&tab=Calendário&token=secret",
    ),
  ).toBe(
    "/demo/financial/multiple?month=2026-09&accounts=itau&tab=Calend%C3%A1rio",
  );
  expect(reviewReturnTo("/works/work_abc123")).toBe("/works/work_abc123");
  expect(reviewReturnTo("https://evil.test/works/work_abc123")).toBe(
    "/conversions?view=batches",
  );
  expect(
    reviewReturnTo("/conversions?view=files&client=c1&period=p1&cursor=next"),
  ).toBe("/conversions?view=files&client=c1&period=p1&cursor=next");
  for (const target of [
    undefined,
    "https://evil.test/conversions",
    "//evil.test/conversions",
    "javascript:alert(1)",
    "/account",
    "https://evil.test/demo/financial/single",
  ]) {
    expect(reviewReturnTo(target)).toBe("/conversions?view=batches");
  }
});
