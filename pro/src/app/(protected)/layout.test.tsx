import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ProtectedLayout from "./layout";

vi.mock("@/components/session-prefetch", () => ({
  SessionPrefetch: () => null,
}));

vi.mock("@/components/pro-session-gate", () => ({
  ProSessionGate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="session-gate">{children}</div>
  ),
}));

describe("ProtectedLayout", () => {
  it("compartilha a proteção da sessão entre a revisão e o dashboard sem afetar a demonstração", () => {
    render(
      <ProtectedLayout>
        <span>Workspace real</span>
      </ProtectedLayout>,
    );

    expect(screen.getByTestId("session-gate")).toContainElement(
      screen.getByText("Workspace real"),
    );
  });
});
