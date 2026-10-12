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
          source: "/api/conversion-runtime",
          destination: "https://api.ofxsimples.com.br/api/conversion-runtime",
        },
        {
          source: "/api/pro/conversion-batches/:path*",
          destination:
            "https://api.ofxsimples.com.br/api/pro/conversion-batches/:path*",
        },
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
          source: "/auth/:path*",
          destination: "https://api.ofxsimples.com.br/auth/:path*",
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
