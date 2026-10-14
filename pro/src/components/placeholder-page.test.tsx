import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DashboardPage } from "./placeholder-page";

describe("DashboardPage", () => {
  it("não apresenta métricas ou lotes fictícios como dados do usuário", () => {
    render(<DashboardPage />);

    expect(
      screen.getByRole("heading", { name: "Dashboard em construção" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Bom trabalho, Ana")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Extratos Setembro/2026"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Criar conversão real" }),
    ).toHaveAttribute("href", "/conversions");
    expect(
      screen.getByRole("link", {
        name: "Ver demonstração com dados fictícios",
      }),
    ).toHaveAttribute("href", "/demo/review/batch-setembro-2026");
  });
});
