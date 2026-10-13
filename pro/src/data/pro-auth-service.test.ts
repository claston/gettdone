import { describe, expect, it, vi } from "vitest";

import { createProAuthService } from "./pro-auth-service";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ProAuthService", () => {
  it("identifica quando não existe uma sessão autenticada", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "unauthorized" }, 401));
    const service = createProAuthService({ apiUrl: "/", fetcher });

    await expect(service.currentUser()).resolves.toBeNull();
  });

  it("faz login same-origin sem expor token ao JavaScript", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        user_id: "usr-1",
        name: "Usuário Canário",
        email: "canario@example.com",
        plan_code: "paid",
      }),
    );
    const service = createProAuthService({ apiUrl: "/", fetcher });

    await expect(
      service.login("canario@example.com", "senha-segura"),
    ).resolves.toMatchObject({ email: "canario@example.com" });
    expect(fetcher).toHaveBeenCalledWith(
      "/auth/pro/session/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-OFX-Pro-Client": "web",
        }),
        body: JSON.stringify({
          email: "canario@example.com",
          password: "senha-segura",
        }),
      }),
    );
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("Authorization");
  });
});
