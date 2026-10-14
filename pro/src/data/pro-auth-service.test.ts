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
      .mockResolvedValueOnce(jsonResponse({ detail: "unauthorized" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "unauthorized" }, 401));
    const service = createProAuthService({ apiUrl: "/", fetcher });

    await expect(service.currentUser()).resolves.toBeNull();
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/auth/session/refresh",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("renova a sessão HttpOnly quando o acesso expirou", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({
          user_id: "usr-1",
          name: "Usuário Canário",
          email: "canario@example.com",
          plan_name: "Profissional",
          quota_remaining: 127,
          quota_limit: 150,
          quota_mode: "pages",
        }),
      );
    const service = createProAuthService({ apiUrl: "/", fetcher });

    await expect(service.currentUser()).resolves.toMatchObject({
      email: "canario@example.com",
      quota_remaining: 127,
    });
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).endsWith("/auth/pro/anonymous-session"),
      ),
    ).toBe(false);
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

  it("encerra a sessão pelo proxy same-origin", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ status: "ok" }));
    const service = createProAuthService({ apiUrl: "/", fetcher });

    await expect(service.logout()).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenCalledWith(
      "/auth/session/logout",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("Authorization");
  });
});
