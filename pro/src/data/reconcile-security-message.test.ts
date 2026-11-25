import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

it("shows the public network limit returned by reconciliation", async () => {
  document.documentElement.innerHTML = readFileSync(resolve(process.cwd(), "../frontend/conciliador.html"), "utf8");
  const message = "O limite de processamento gratuito desta rede foi atingido. Entre na sua conta ou aguarde a renovação do limite.";
  vi.stubGlobal("OfxSession", { getCurrentUser: async () => null });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: url.endsWith("/auth/anonymous-session"), status: url.endsWith("/reconcile") ? 429 : 200,
    json: async () => ({ detail: { code: "public_request_limit", message } }),
    text: async () => JSON.stringify({ detail: { code: "public_request_limit", message } }),
  })));
  window.eval(readFileSync(resolve(process.cwd(), "../frontend/index.js"), "utf8"));
  for (const id of ["bank-file-input", "sheet-file-input"]) {
    const input = document.getElementById(id)!;
    Object.defineProperty(input, "files", { value: [new File(["synthetic"], "sample.csv", { type: "text/csv" })] });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
  document.getElementById("show-report-btn")!.click();
  await vi.waitFor(() => expect(document.getElementById("upload-validation")).toHaveTextContent(message));
});
