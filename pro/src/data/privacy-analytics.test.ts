// @vitest-environment node
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const source = readFileSync("../frontend/privacy-consent.js", "utf8");

function setup(prefs?: object, path = "/planos.html") {
  const dom = new JSDOM(
    "<!doctype html><html data-ofx-surface='pro'><body></body></html>",
    {
      url: "https://ofxsimples.com.br" + path,
      runScripts: "outside-only",
    },
  );
  const { window } = dom;
  const fetcher = vi.fn().mockResolvedValue({ ok: true });
  window.fetch = fetcher;
  window.crypto.randomUUID = randomUUID;
  if (prefs)
    window.document.cookie =
      "ofx_privacy_preferences_v1=" +
      encodeURIComponent(JSON.stringify(prefs)) +
      "; Path=/";
  window.eval(source);
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return { dom, window, fetcher };
}

describe("consent-gated explicit analytics", () => {
  it.each([
    undefined,
    { analytics: false },
    { analytics: "true" },
    { marketing: true },
  ])("does not collect without boolean analytics consent: %j", (prefs) => {
    const { dom, fetcher, window } = setup(prefs);
    window.OfxAnalytics!.capture("preview_viewed");
    expect(fetcher).not.toHaveBeenCalled();
    expect(window.document.cookie).not.toContain("ofx_analytics_id");
    dom.window.close();
  });

  it("sends one sanitized page view and rejects arbitrary events", () => {
    const { dom, window, fetcher } = setup(
      { analytics: true },
      "/conversions/secret-company?token=secret#secret",
    );
    window.OfxAnalytics!.pageView();
    window.OfxAnalytics!.capture("SECRET_TRANSACTION");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      event_type: "page_view",
      page_path: "/conversions/:id",
    });
    expect(window.document.cookie).toContain("ofx_analytics_id");
    expect(window.document.querySelectorAll("script")).toHaveLength(0);
    dom.window.close();
  });

  it("accepts, revokes immediately, removes identity and creates a fresh identity on reacceptance", () => {
    const { dom, window, fetcher } = setup();
    window.document.getElementById("privacy-accept")!.click();
    const firstCookie = window.document.cookie;
    expect(fetcher).toHaveBeenCalledTimes(1);
    window.document.getElementById("privacy-preferences-link")!.click();
    window.document.getElementById("privacy-reject")!.click();
    expect(window.document.cookie).not.toContain("ofx_analytics_id");
    window.OfxAnalytics!.capture("upload_started");
    expect(fetcher).toHaveBeenCalledTimes(1);
    window.document.getElementById("privacy-preferences-link")!.click();
    (
      window.document.getElementById("privacy-analytics") as HTMLInputElement
    ).checked = true;
    window.document.getElementById("privacy-save")!.click();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(window.document.cookie).not.toBe(firstCookie);
    dom.window.close();
  });

  it("does not interrupt the product when transport throws", () => {
    const { dom, window, fetcher } = setup({ analytics: true });
    fetcher.mockImplementation(() => {
      throw new Error("offline");
    });
    expect(() => window.OfxAnalytics!.capture("upload_started")).not.toThrow();
    dom.window.close();
  });

  it("counts navigation between documents without sending their identifiers", () => {
    const { dom, window, fetcher } = setup(
      { analytics: true },
      "/review/private-one",
    );
    window.history.pushState({}, "", "/review/private-two");
    window.OfxAnalytics!.pageView();
    expect(fetcher).toHaveBeenCalledTimes(2);
    for (const call of fetcher.mock.calls) {
      expect(JSON.parse(call[1].body).page_path).toBe("/review/:id");
      expect(call[1].body).not.toContain("private");
    }
    dom.window.close();
  });
});
