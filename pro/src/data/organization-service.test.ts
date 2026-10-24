import { describe, expect, it, vi } from "vitest";
import { createOrganizationService } from "./organization-service";

describe("OrganizationService", () => {
  it("combina filtros independentes e usa o cliente autenticado nas escritas", async () => {
    const request = vi
      .fn()
      .mockResolvedValue(Response.json({ items: [], next_cursor: null }));
    const service = createOrganizationService({ apiClient: { request } });
    await service.listConversions(
      { client: "unassigned", period: "period/a", account: "" },
      "next+page",
    );
    const url = new URL(request.mock.calls[0][0], "https://pro.example.test");
    expect(url.searchParams.get("unassigned")).toBe("true");
    expect(url.searchParams.get("client_id")).toBeNull();
    expect(url.searchParams.get("period_id")).toBe("period/a");
    expect(url.searchParams.get("cursor")).toBe("next+page");
    request.mockResolvedValueOnce(
      Response.json({
        version: 2,
        client_id: null,
        account_id: null,
        period_ids: ["p"],
      }),
    );
    await service.organize("batch/a", "job/b", {
      client_id: null,
      account_id: null,
      period_ids: ["p"],
      version: 1,
    });
    expect(request.mock.calls[1][0]).toBe(
      "/api/pro/organization/batches/batch%2Fa/jobs/job%2Fb/context",
    );
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({
      client_id: null,
      account_id: null,
      period_ids: ["p"],
      expected_version: 1,
    });
    expect(request.mock.calls[1][1].method).toBe("PUT");
  });

  it("apresenta conflitos sem converter uma falha em sucesso", async () => {
    const request = vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { detail: "Atualize a lista antes de salvar novamente." },
          { status: 409 },
        ),
      );
    const service = createOrganizationService({ apiClient: { request } });
    await expect(
      service.organize("batch", "job", {
        client_id: null,
        account_id: null,
        period_ids: [],
        version: 0,
      }),
    ).rejects.toThrow("Atualize a lista");
  });
});
