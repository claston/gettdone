import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StandardLayout from "./layout";

vi.mock("@/components/pro-session-gate", () => ({
  ProSessionGate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="session-gate">{children}</div>
  ),
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));

describe("StandardLayout", () => {
  it("protege toda a área da aplicação com a sessão PRO", () => {
    render(
      <StandardLayout>
        <span>Conteúdo real</span>
      </StandardLayout>,
    );

    expect(screen.getByTestId("session-gate")).toContainElement(
      screen.getByTestId("app-shell"),
    );
    expect(screen.getByText("Conteúdo real")).toBeInTheDocument();
  });
});
