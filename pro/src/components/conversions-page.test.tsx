import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ConversionsPage } from "./conversions-page";
import { createOrganizationService } from "@/data/organization-service";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

it("mantém lotes e arquivos independentes e oculta cadastros e filtros de períodos", async () => {
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
  await screen.findByRole("heading", { name: "Todas as conversões" });
  expect(
    screen.queryByLabelText("Filtrar por cliente"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: /Cadastrar/ }),
  ).not.toBeInTheDocument();
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      expect.stringContaining("include_organization=false"),
      expect.anything(),
    ),
  );
  expect(request.mock.calls.some(([path]) => path.endsWith("/catalog"))).toBe(
    false,
  );
  fireEvent.click(screen.getByRole("button", { name: "Lotes" }));
  expect(
    screen.queryByRole("heading", { name: "Todas as conversões" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Todos os arquivos" }));
  expect(
    screen.getByRole("heading", { name: "Todas as conversões" }),
  ).toBeInTheDocument();
  await waitFor(() => expect(window.location.search).toContain("view=files"));
});
