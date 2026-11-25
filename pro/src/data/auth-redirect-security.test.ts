import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

const origin = "https://www.ofxsimples.com.br";

async function runPage(file: string, next: string) {
  const location = { origin, hostname: "www.ofxsimples.com.br", protocol: "https:", port: "",
    search: `?next=${encodeURIComponent(next)}`, pathname: `/${file}.html`, href: "" };
  const window = { location, OfxSession: { getCurrentUser: async () => ({ user_id: "synthetic" }) },
    history: { replaceState: vi.fn() }, setTimeout: (fn: () => void) => fn() };
  runInNewContext(readFileSync(resolve(process.cwd(), `../frontend/${file}.js`), "utf8"), {
    window, document: { getElementById: () => null }, URL, URLSearchParams,
    HTMLElement: class {}, HTMLInputElement: class {}, sessionStorage: { setItem: vi.fn() },
  });
  await vi.waitFor(() => expect(location.href).not.toBe(""));
  return new URL(location.href, origin);
}

describe.each(["login", "signup", "auth-callback"])("%s redirect boundary", (file) => {
  it.each(["/\\outside.invalid/path", "//outside.invalid/path", "/\t/outside.invalid/path",
    "/\n/outside.invalid/path", "https://outside.invalid/path", "javascript:alert(1)"])(
    "keeps %j on the application origin", async (next) => {
      const target = await runPage(file, next);
      expect(target.origin).toBe(origin);
      expect(target.pathname).toBe(file === "signup" ? "/ofx-convert.html" : "/client-area.html");
    },
  );
  it("preserves a local path, query and fragment", async () => {
    expect((await runPage(file, "/client-area.html?tab=files#recent")).href)
      .toBe(`${origin}/client-area.html?tab=files#recent`);
  });
});

it("removes legacy browser credentials without submitting them for migration", async () => {
  const fetch = vi.fn();
  const removeItem = vi.fn();
  const window = { location: { origin, hostname: "www.ofxsimples.com.br", protocol: "https:", port: "" },
    fetch, OfxSession: undefined as unknown as { ready: () => Promise<void> } };
  const document = { cookie: "ofxsimples_user_token=retired-secret" };
  runInNewContext(readFileSync(resolve(process.cwd(), "../frontend/auth-session.js"), "utf8"), {
    window, document, localStorage: { removeItem, getItem: () => "retired-secret" },
  });
  await window.OfxSession.ready();
  expect(removeItem).toHaveBeenCalledWith("ofxsimples_user_token");
  expect(document.cookie).toContain("Max-Age=0");
  expect(fetch).not.toHaveBeenCalled();
});
