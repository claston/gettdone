import { describe, expect, it } from "vitest";

import { buildBackendProxyRewrites } from "./backend-proxy";

describe("buildBackendProxyRewrites", () => {
  it("mantém o modo mock quando o backend não está configurado", () => {
    expect(buildBackendProxyRewrites(undefined)).toEqual([]);
    expect(buildBackendProxyRewrites("   ")).toEqual([]);
  });

  it("expõe somente as rotas usadas pelo workspace pelo mesmo domínio", () => {
    expect(buildBackendProxyRewrites("https://api.ofxsimples.com.br/")).toEqual(
      [
        {
          source: "/telemetry/events",
          destination: "https://api.ofxsimples.com.br/telemetry/events",
        },
        {
          source: "/api/conversion-runtime",
          destination: "https://api.ofxsimples.com.br/api/conversion-runtime",
        },
        {
          source: "/api/pro/conversion-options",
          destination:
            "https://api.ofxsimples.com.br/api/pro/conversion-options",
        },
        {
          source: "/api/pro/conversion-batches/:path*",
          destination:
            "https://api.ofxsimples.com.br/api/pro/conversion-batches/:path*",
        },
        {
          source: "/api/pro/conversions",
          destination: "https://api.ofxsimples.com.br/api/pro/conversions",
        },
        {
          source: "/api/pro/receipts/:path*",
          destination: "https://api.ofxsimples.com.br/api/pro/receipts/:path*",
        },
        {
          source: "/api/pro/works/:path*",
          destination: "https://api.ofxsimples.com.br/api/pro/works/:path*",
        },
        ...[
          "catalog",
          "clients/:path*",
          "accounts/:path*",
          "periods/:path*",
          "conversions",
          "batches/:path*",
          "contexts/bulk",
        ].map((path) => ({
          source: `/api/pro/organization/${path}`,
          destination: `https://api.ofxsimples.com.br/api/pro/organization/${path}`,
        })),
        {
          source: "/api/pro/convert-edits/:path*",
          destination:
            "https://api.ofxsimples.com.br/api/pro/convert-edits/:path*",
        },
        {
          source: "/api/conversion-batches/:path*",
          destination:
            "https://api.ofxsimples.com.br/api/conversion-batches/:path*",
        },
        {
          source: "/auth/me",
          destination: "https://api.ofxsimples.com.br/auth/me",
        },
        {
          source: "/auth/pro/anonymous-session",
          destination:
            "https://api.ofxsimples.com.br/auth/pro/anonymous-session",
        },
        {
          source: "/auth/pro/session/login",
          destination: "https://api.ofxsimples.com.br/auth/pro/session/login",
        },
        {
          source: "/auth/session/refresh",
          destination: "https://api.ofxsimples.com.br/auth/session/refresh",
        },
        {
          source: "/auth/session/logout",
          destination: "https://api.ofxsimples.com.br/auth/session/logout",
        },
        {
          source: "/convert-edits/:path*",
          destination: "https://api.ofxsimples.com.br/convert-edits/:path*",
        },
        {
          source: "/convert-report/:path*",
          destination: "https://api.ofxsimples.com.br/convert-report/:path*",
        },
      ],
    );
  });

  it("rejeita destino inseguro ou com credenciais", () => {
    expect(() => buildBackendProxyRewrites("ftp://api.example.test")).toThrow(
      "http ou https",
    );
    expect(() =>
      buildBackendProxyRewrites("https://user:secret@api.example.test"),
    ).toThrow("credenciais");
  });
});
