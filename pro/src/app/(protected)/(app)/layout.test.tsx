import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StandardLayout from "./layout";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));

describe("StandardLayout", () => {
  it("mantém o shell dentro da sessão compartilhada com a revisão", () => {
    render(
      <StandardLayout>
        <span>Conteúdo real</span>
      </StandardLayout>,
    );

    expect(screen.getByTestId("app-shell")).toContainElement(
      screen.getByText("Conteúdo real"),
    );
    expect(screen.getByText("Conteúdo real")).toBeInTheDocument();
  });
});
