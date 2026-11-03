import { expect, it } from "vitest";
import { decimalCents, financialMoney } from "./financial-center-service";

it("preserva centavos acima do limite inteiro seguro e sinais", () => {
  expect(decimalCents("99999999999999.99")).toBe(9999999999999999n);
  expect(decimalCents("-0.01")).toBe(-1n);
  expect(financialMoney(-275n, "BRL")).toContain("2,75");
  expect(financialMoney(9999999999999999n, "BRL")).toContain(
    "99.999.999.999.999,99",
  );
  expect(() => decimalCents("1.001")).toThrow();
});
