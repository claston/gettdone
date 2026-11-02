import { afterEach, expect, it, vi } from "vitest";
const notFound = vi.hoisted(() =>
  vi.fn(() => {
    throw new Error("404");
  }),
);
vi.mock("next/navigation", () => ({ notFound }));
afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});
it("bloqueia o protótipo por padrão, inclusive acesso direto", async () => {
  vi.stubEnv("PRO_FINANCIAL_CENTER_DEMO", "false");
  const { default: Layout } = await import("./layout");
  expect(() => Layout({ children: "demo" })).toThrow("404");
});
it("permite apenas a demonstração quando explicitamente habilitada", async () => {
  vi.stubEnv("PRO_FINANCIAL_CENTER_DEMO", "true");
  const { default: Layout } = await import("./layout");
  expect(Layout({ children: "demo" })).toBe("demo");
});
