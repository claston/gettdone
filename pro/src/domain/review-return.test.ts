import { expect, it } from "vitest";
import { reviewReturnTo } from "./review-return";

it("preserva filtros de conversões e rejeita destinos externos", () => {
  expect(
    reviewReturnTo("/conversions?view=files&client=c1&period=p1&cursor=next"),
  ).toBe("/conversions?view=files&client=c1&period=p1&cursor=next");
  for (const target of [
    undefined,
    "https://evil.test/conversions",
    "//evil.test/conversions",
    "javascript:alert(1)",
    "/account",
  ]) {
    expect(reviewReturnTo(target)).toBe("/conversions?view=batches");
  }
});
