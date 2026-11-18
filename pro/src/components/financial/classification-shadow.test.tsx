import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ClassificationShadow } from "./classification-shadow";
import { shadowRequest, type ShadowRun } from "@/data/classification-shadow";

vi.mock("@/data/classification-shadow", () => ({ shadowRequest: vi.fn() }));
const source = {
  id: "m1",
  version: 1,
  kind: "movement" as const,
  label: "Tarifa bancária",
};
const result: ShadowRun = {
  id: "r1",
  state: "ready",
  stale: false,
  engine_version: "test",
  expires_at: new Date(Date.now() + 600000).toISOString(),
  items: [
    {
      id: "m1",
      description: "Tarifa bancária",
      label: "Tarifa",
      status: "suggested",
      evidence: "Descrição bancária",
      candidates: [],
    },
  ],
  feedback: [],
  metrics: { agree: 0, disagree: 0, unknown: 0 },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(shadowRequest).mockResolvedValue(result);
});

it("evaluates only after an explicit request and sends feedback to the isolated route", async () => {
  render(<ClassificationShadow clientId="c1" sources={[source]} />);
  fireEvent.click(screen.getByText("Avaliar classificação experimental"));
  fireEvent.change(screen.getByLabelText("Documento ou lançamento"), {
    target: { value: "movement:m1" },
  });
  expect(shadowRequest).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Avaliar classificação" }),
  );
  await screen.findByText("Descrição bancária");
  expect(shadowRequest).toHaveBeenCalledWith("c1", "/runs", {
    source_kind: "movement",
    source_id: "m1",
    source_version: 1,
  });
  fireEvent.click(screen.getByRole("button", { name: "Concordo" }));
  await waitFor(() =>
    expect(shadowRequest).toHaveBeenLastCalledWith("c1", "/runs/r1/feedback", {
      item_id: "m1",
      verdict: "agree",
    }),
  );
});

it("hides old suggestions when the source version changes", async () => {
  const view = render(
    <ClassificationShadow clientId="c1" sources={[source]} />,
  );
  fireEvent.click(screen.getByText("Avaliar classificação experimental"));
  fireEvent.change(screen.getByLabelText("Documento ou lançamento"), {
    target: { value: "movement:m1" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Avaliar classificação" }),
  );
  await screen.findByText("Descrição bancária");
  view.rerender(
    <ClassificationShadow
      clientId="c1"
      sources={[{ ...source, version: 2 }]}
    />,
  );
  expect(screen.queryByText("Descrição bancária")).toBeNull();
});
