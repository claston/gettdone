import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ConversionsPage } from "./conversions-page";
import { createOrganizationService } from "@/data/organization-service";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

it("abre lotes sem consultar organização e preserva filtros ao alternar visões", async () => {
  const request = vi.fn(async (path: string) =>
    Response.json(
      path.endsWith("/catalog")
        ? {
            clients: [
              { id: "c1", name: "Cliente A", tax_id: null, trade_name: "" },
            ],
            accounts: [],
            periods: [],
          }
        : { items: [], next_cursor: null },
    ),
  );
  const batches = {
    list: vi.fn().mockResolvedValue({ items: [], nextCursor: null }),
  };
  render(
    <ConversionsPage
      organization={createOrganizationService({ apiClient: { request } })}
      batches={batches}
    />,
  );
  await screen.findByText("Nenhum lote de conversão encontrado.");
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Todos os arquivos" }));
  const filter = await screen.findByLabelText("Filtrar por cliente");
  fireEvent.change(filter, { target: { value: "c1" } });
  await waitFor(() => expect(window.location.search).toContain("client=c1"));
  fireEvent.click(screen.getByRole("button", { name: "Lotes" }));
  expect(
    screen.queryByRole("heading", { name: "Todas as conversões" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Todos os arquivos" }));
  expect(screen.getByLabelText("Filtrar por cliente")).toHaveValue("c1");
  await waitFor(() => expect(window.location.search).toContain("client=c1"));
});
