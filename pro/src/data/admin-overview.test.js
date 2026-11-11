import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { expect, it, vi } from "vitest";

const html = readFileSync(
  resolve(process.cwd(), "../frontend/admin.html"),
  "utf8",
);
const script = readFileSync(
  resolve(process.cwd(), "../frontend/admin.js"),
  "utf8",
);
const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status });
const overview = {
  summary: { conversions_total: 42 },
  checkout_funnel: {},
  commercial_interest: [],
  end_at: "2026-11-11T12:00:00Z",
};

function open(fetcher) {
  const dom = new JSDOM(html, {
    url: "https://ofx.test/admin.html",
    runScripts: "outside-only",
  });
  dom.window.fetch = fetcher;
  dom.window.eval(script);
  return dom;
}

it("loads totals first and fetches details only on explicit request", async () => {
  const paths = [];
  const dom = open(
    vi.fn(async (url) => {
      paths.push(new URL(url).pathname);
      return json(overview);
    }),
  );
  try {
    const doc = dom.window.document;
    await vi.waitFor(() =>
      expect(doc.querySelector("#dashboard-summary").textContent).toContain(
        "42",
      ),
    );
    expect(paths).toEqual(["/admin/me", "/admin/dashboard/overview"]);
    expect(
      doc
        .querySelector("#dashboard-details-content")
        .classList.contains("hidden"),
    ).toBe(true);
    doc.querySelector("#dashboard-details-btn").click();
    await vi.waitFor(() => expect(paths).toContain("/admin/dashboard"));
    await vi.waitFor(() =>
      expect(
        doc
          .querySelector("#dashboard-details-content")
          .classList.contains("hidden"),
      ).toBe(false),
    );
    doc.querySelector("#dashboard-refresh-btn").click();
    await vi.waitFor(() =>
      expect(
        paths.filter((path) => path === "/admin/dashboard/overview"),
      ).toHaveLength(2),
    );
    expect(paths.filter((path) => path === "/admin/dashboard")).toHaveLength(1);
    expect(
      doc
        .querySelector("#dashboard-details-content")
        .classList.contains("hidden"),
    ).toBe(true);
  } finally {
    dom.window.close();
  }
});

it("keeps details unavailable until the overview succeeds without falling back to a heavy request", async () => {
  const paths = [];
  const dom = open(
    vi.fn(async (url) => {
      const path = new URL(url).pathname;
      paths.push(path);
      return path === "/admin/me"
        ? json({})
        : json({ detail: "Resumo indisponível" }, 404);
    }),
  );
  try {
    await vi.waitFor(() =>
      expect(
        dom.window.document.querySelector("#dashboard-status").textContent,
      ).toContain("Resumo indisponível"),
    );
    expect(paths).toEqual(["/admin/me", "/admin/dashboard/overview"]);
    expect(
      dom.window.document.querySelector("#dashboard-details-btn").disabled,
    ).toBe(true);
  } finally {
    dom.window.close();
  }
});

it("ignores a late overview from the previous period", async () => {
  let finishOld;
  let reads = 0;
  const dom = open(
    vi.fn(async (url) => {
      if (!String(url).includes("/overview")) return json({});
      if (++reads === 1)
        return new Promise((resolve) => {
          finishOld = resolve;
        });
      return json({ ...overview, summary: { conversions_total: 99 } });
    }),
  );
  try {
    await vi.waitFor(() => expect(finishOld).toBeTypeOf("function"));
    const doc = dom.window.document;
    const filter = doc.querySelector("#dashboard-period");
    filter.value = "7";
    filter.dispatchEvent(new dom.window.Event("change"));
    await vi.waitFor(() =>
      expect(doc.querySelector("#dashboard-summary").textContent).toContain(
        "99",
      ),
    );
    finishOld(json(overview));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(doc.querySelector("#dashboard-summary").textContent).not.toContain(
      "42",
    );
  } finally {
    dom.window.close();
  }
});

it.each(["filter", "logout"])(
  "ignores late details after %s",
  async (action) => {
    let finishDetails;
    const dom = open(
      vi.fn(async (url) => {
        if (new URL(url).pathname === "/admin/dashboard")
          return new Promise((resolve) => {
            finishDetails = resolve;
          });
        return json(overview);
      }),
    );
    try {
      const doc = dom.window.document;
      await vi.waitFor(() =>
        expect(doc.querySelector("#dashboard-details-btn").disabled).toBe(
          false,
        ),
      );
      doc.querySelector("#dashboard-details-btn").click();
      await vi.waitFor(() => expect(finishDetails).toBeTypeOf("function"));
      if (action === "filter") {
        const filter = doc.querySelector("#dashboard-period");
        filter.value = "7";
        filter.dispatchEvent(new dom.window.Event("change"));
        await vi.waitFor(() =>
          expect(doc.querySelector("#dashboard-details-btn").disabled).toBe(
            false,
          ),
        );
      } else {
        doc.querySelector("#admin-logout-btn").click();
        await vi.waitFor(() =>
          expect(
            doc.querySelector("#admin-navigation").classList.contains("hidden"),
          ).toBe(true),
        );
      }
      finishDetails(json({ ...overview, recent_attention: [] }));
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(
        doc
          .querySelector("#dashboard-details-content")
          .classList.contains("hidden"),
      ).toBe(true);
      expect(
        doc
          .querySelector("#dashboard-details-btn")
          .getAttribute("aria-expanded"),
      ).toBe("false");
    } finally {
      dom.window.close();
    }
  },
);
