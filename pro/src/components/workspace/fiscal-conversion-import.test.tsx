import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { loadFinancialMonth } from "@/data/financial-center-service";
import { uploadFiscalNote } from "@/data/fiscal-service";
import { FiscalConversionImport } from "./fiscal-conversion-import";

const { catalog } = vi.hoisted(() => ({ catalog: vi.fn() }));
vi.mock("@/data/organization-service", () => ({
  createOrganizationService: () => ({ catalog }),
}));
vi.mock("@/data/financial-center-service", () => ({
  loadFinancialMonth: vi.fn(),
}));
vi.mock("@/data/fiscal-service", () => ({ uploadFiscalNote: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  catalog.mockResolvedValue({
    clients: [{ id: "c1", name: "Empresa de teste" }],
    accounts: [],
    periods: [],
  });
  vi.mocked(loadFinancialMonth).mockResolvedValue({
    features: { invoices: true },
  } as Awaited<ReturnType<typeof loadFinancialMonth>>);
  vi.mocked(uploadFiscalNote).mockResolvedValue({ id: "i1", state: "queued" });
});

it("só importa o original após escolher a empresa e confirmar, sem associar movimentos", async () => {
  const loadDocument = vi
    .fn()
    .mockResolvedValue(
      new Blob(["nota sintética"], { type: "application/pdf" }),
    );
  render(
    <FiscalConversionImport
      conversionId="j1"
      filename="nota.pdf"
      loadDocument={loadDocument}
    />,
  );
  const select = await screen.findByRole("combobox", {
    name: "Empresa da nota fiscal",
  });
  expect(select).toHaveValue("");
  expect(uploadFiscalNote).not.toHaveBeenCalled();
  expect(loadDocument).not.toHaveBeenCalled();
  fireEvent.change(select, { target: { value: "c1" } });
  const submit = await screen.findByRole("button", {
    name: "Importar na gestão fiscal",
  });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await screen.findByText(/Importação da nota iniciada/);
  expect(loadDocument).toHaveBeenCalledWith("j1");
  expect(uploadFiscalNote).toHaveBeenCalledWith(
    "c1",
    expect.objectContaining({ name: "nota.pdf", type: "application/pdf" }),
  );
});

it("não oferece importação quando a gestão fiscal está desabilitada", async () => {
  vi.mocked(loadFinancialMonth).mockResolvedValue({
    features: { invoices: false },
  } as Awaited<ReturnType<typeof loadFinancialMonth>>);
  const loadDocument = vi.fn();
  render(
    <FiscalConversionImport
      conversionId="j1"
      filename="nota.pdf"
      loadDocument={loadDocument}
    />,
  );
  fireEvent.change(await screen.findByRole("combobox"), {
    target: { value: "c1" },
  });
  await screen.findByText(/gestão fiscal ainda não está habilitada/);
  expect(
    screen.queryByRole("button", { name: "Importar na gestão fiscal" }),
  ).not.toBeInTheDocument();
  expect(uploadFiscalNote).not.toHaveBeenCalled();
  expect(loadDocument).not.toHaveBeenCalled();
});

it("mantém a nota pendente quando o original expirou", async () => {
  const loadDocument = vi
    .fn()
    .mockRejectedValue(new Error("O arquivo original expirou."));
  render(
    <FiscalConversionImport
      conversionId="j1"
      filename="nota.pdf"
      loadDocument={loadDocument}
    />,
  );
  fireEvent.change(await screen.findByRole("combobox"), {
    target: { value: "c1" },
  });
  const submit = await screen.findByRole("button", {
    name: "Importar na gestão fiscal",
  });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "O arquivo original expirou.",
  );
  expect(uploadFiscalNote).not.toHaveBeenCalled();
});
