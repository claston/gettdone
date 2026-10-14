import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ReviewLayout from "./layout";

vi.mock("@/components/pro-session-gate", () => ({
  ProSessionGate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="session-gate">{children}</div>
  ),
}));

describe("ReviewLayout", () => {
  it("protege o workspace real sem afetar as rotas de demonstração", () => {
    render(
      <ReviewLayout>
        <span>Workspace real</span>
      </ReviewLayout>,
    );

    expect(screen.getByTestId("session-gate")).toContainElement(
      screen.getByText("Workspace real"),
    );
  });
});
