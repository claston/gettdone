import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { officeService, type ImportPreparation } from "@/data/office-service";
import { organizationService } from "@/data/organization-service";
import { useImportPreparation } from "./import-preparation";

vi.mock("@/data/office-service", () => ({
  officeService: { retention: vi.fn(), prepare: vi.fn() },
}));
vi.mock("@/data/organization-service", () => ({
  organizationService: { organize: vi.fn() },
}));
const document = {
  job_id: "job_a",
  batch_id: "batch_a",
  filename: "nota.pdf",
  is_bank: false,
  context: { client_id: null, account_id: null, version: 0, period_ids: [] },
  catalog: {
    clients: [
      { id: "client_a", name: "Cliente A", trade_name: "", tax_id: null },
    ],
    accounts: [],
    periods: [],
  },
  suggestions: { clients: [], accounts: [], period: null },
  automatically_associated: false,
  matching: null,
} satisfies ImportPreparation;
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(officeService.retention).mockResolvedValue({
    read_only: false,
    retained_while_paid: true,
    delete_after: null,
    grace_days: 30,
  });
  vi.mocked(officeService.prepare).mockResolvedValue(document);
});

it("saves the selected client with CAS as part of the single confirmation", async () => {
  const { result } = renderHook(() =>
    useImportPreparation(true, "job_a", "client_a"),
  );
  await waitFor(() => expect(result.current.data).not.toBeNull());
  expect(organizationService.organize).not.toHaveBeenCalled();
  vi.mocked(organizationService.organize).mockResolvedValue({
    ...document.context,
    client_id: "client_a",
    version: 1,
  });
  vi.mocked(officeService.prepare).mockResolvedValue({
    ...document,
    context: { ...document.context, client_id: "client_a", version: 1 },
  });
  await act(async () => {
    expect(await result.current.confirmContext("job_a")).toBe(1);
  });
  expect(organizationService.organize).toHaveBeenCalledWith(
    "batch_a",
    "job_a",
    { ...document.context, client_id: "client_a" },
  );
  await act(async () => {
    expect(await result.current.confirmContext("job_a")).toBe(1);
  });
  expect(organizationService.organize).toHaveBeenCalledTimes(1);
});

it("never edits or auto-associates during the thirty-day read-only period", async () => {
  vi.mocked(officeService.retention).mockResolvedValue({
    read_only: true,
    retained_while_paid: false,
    delete_after: "2026-12-27T00:00:00Z",
    grace_days: 30,
  });
  const { result } = renderHook(() =>
    useImportPreparation(true, "job_a", "client_a"),
  );
  await waitFor(() => expect(result.current.data).not.toBeNull());
  expect(officeService.prepare).toHaveBeenCalledWith(
    "job_a",
    true,
    expect.any(AbortSignal),
  );
  await expect(result.current.confirmContext("job_a")).rejects.toThrow();
  expect(organizationService.organize).not.toHaveBeenCalled();
});

it("blocks confirmation of another file and does not hide association conflicts", async () => {
  const { result } = renderHook(() =>
    useImportPreparation(true, "job_a", "client_a"),
  );
  await waitFor(() => expect(result.current.data).not.toBeNull());
  await expect(result.current.confirmContext("job_b")).rejects.toThrow();
  vi.mocked(organizationService.organize).mockRejectedValue(
    new Error("A associação mudou."),
  );
  await expect(result.current.confirmContext("job_a")).rejects.toThrow(
    "A associação mudou.",
  );
  expect(result.current.data?.context.version).toBe(0);
});
