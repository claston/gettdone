import { describe, expect, it, vi } from "vitest";

import Home from "./page";

const redirect = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ redirect }));

describe("Home", () => {
  it("abre o fluxo real de conversão em vez do dashboard mockado", () => {
    Home();

    expect(redirect).toHaveBeenCalledWith("/conversions");
  });
});
