import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { afterEach, expect, it, vi } from "vitest";

const number = "55" + "11" + "9" + "0".repeat(8);
const plans = ["Essencial", "Profissional", "Escritório"].map((name, i) => ({
  code: ["essencial", "profissional", "escritorio"][i],
  name,
  price_cents: 2990 + i * 1000,
  quota_limit: 150,
  max_upload_size_bytes: 10485760,
}));
const paid = {
  email: "client@example.com",
  plan_code: "essencial",
  plan_name: "Essencial",
  quota_mode: "pages",
  quota_remaining: 0,
};
const free = {
  email: "client@example.com",
  quota_mode: "conversion",
  plan_code: null,
};
const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status });
const opened = [];
afterEach(async () => {
  // Finish response-body promises before disposing each document and its timers.
  await new Promise((resolve) => setTimeout(resolve, 0));
  opened.splice(0).forEach((dom) => dom.window.close());
});

function open(
  page,
  {
    me = paid,
    config = { whatsapp_support_number: number },
    override,
    query = "",
  } = {},
) {
  const root = resolve(process.cwd(), "../frontend");
  const dom = new JSDOM(readFileSync(resolve(root, `${page}.html`), "utf8"), {
    url: `https://ofx.test/${page}.html${query}`,
    runScripts: "outside-only",
  });
  opened.push(dom);
  dom.window.fetch = vi.fn(async (url) => {
    const path = new URL(url).pathname;
    const replacement = override?.(path);
    if (replacement !== undefined) return replacement;
    if (path === "/public-config") return json(config);
    if (path === "/auth/me") return json(me);
    if (path === "/plans") return json({ items: plans });
    return json({ items: [] });
  });
  dom.window.OfxSession = {
    apiBase: "https://ofx.test",
    ready: async () => {},
    getCurrentUser: async () => me,
    request: dom.window.fetch,
  };
  for (const node of dom.window.document.querySelectorAll("script[src]")) {
    const file = node.getAttribute("src").split("?")[0].split("/").pop();
    if (file === "whatsapp-support.js" || file === `${page}.js`) {
      dom.window.eval(readFileSync(resolve(root, file), "utf8"));
    }
  }
  return dom;
}

function link(dom, id) {
  return dom.window.document.getElementById(id);
}
async function visible(dom, id) {
  await vi.waitFor(() => {
    expect(link(dom, id)).not.toBeNull();
    expect(link(dom, id).closest(".hidden")).toBeNull();
    expect(link(dom, id).getAttribute("href")).toMatch(/^https:\/\/wa\.me\//);
  });
  return new URL(link(dom, id).href);
}

it("shows paid support even with exhausted quota and unavailable history", async () => {
  const dom = open("client-area", {
    override: (path) =>
      path === "/client/conversions" ? new Promise(() => {}) : undefined,
  });
  const url = await visible(dom, "client-whatsapp-link");
  expect(url.pathname).toBe(`/${number}`);
  expect(url.searchParams.get("text")).toBe(
    "Olá! Sou cliente do OFX Simples, tenho o plano Essencial e preciso de suporte.",
  );
  expect(
    dom.window.document.querySelector("#client-support").previousElementSibling
      .className,
  ).toBe("quick-metrics");
});

it("describes WhatsApp for paid plans and email for a future free plan", async () => {
  const freePlan = {
    ...plans[0],
    code: "free",
    name: "Gratuito",
    price_cents: 0,
  };
  const dom = open("planos", {
    config: { whatsapp_support_number: null },
    override: (path) =>
      path === "/plans" ? json({ items: [...plans, freePlan] }) : undefined,
  });
  await vi.waitFor(() =>
    expect(dom.window.document.querySelectorAll(".plan-support")).toHaveLength(
      4,
    ),
  );
  const cards = [...dom.window.document.querySelectorAll(".plan-card")];
  for (const card of cards) {
    const isFree = card.querySelector("h2").textContent === "Gratuito";
    const support = card.querySelector(".plan-support");
    expect(support.textContent.trim()).toBe(
      isFree ? "Suporte por e-mail" : "Suporte por WhatsApp",
    );
    expect(
      support.querySelector(
        isFree ? ".plan-support-icon-email" : ".plan-support-icon-whatsapp",
      ),
    ).not.toBeNull();
    expect(support.querySelector("svg").getAttribute("aria-hidden")).toBe(
      "true",
    );
    expect(support.querySelector("a")).toBeNull();
  }
  expect(cards[0].querySelector("h2").textContent).toBe("Gratuito");
  expect(dom.window.document.body.textContent).not.toContain(
    "Suporte por contato",
  );
});

it.each([
  free,
  { ...free, pending_plan: "profissional" },
  { ...free, ended_plan: "essencial" },
  { ...paid, plan_code: null },
])(
  "does not show customer support without an active paid identity: %j",
  async (me) => {
    const dom = open("client-area", { me });
    await vi.waitFor(() =>
      expect(link(dom, "profile-email").textContent).toBe(me.email),
    );
    expect(link(dom, "client-support").classList.contains("hidden")).toBe(true);
    expect(link(dom, "client-whatsapp-link").hasAttribute("href")).toBe(false);
  },
);

it("keeps support hidden until authentication finishes", async () => {
  let resolveMe;
  const response = new Promise((resolve) => {
    resolveMe = resolve;
  });
  const dom = open("client-area", {
    override: (path) => (path === "/auth/me" ? response : undefined),
  });
  await vi.waitFor(() =>
    expect(dom.window.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/public-config"),
      expect.anything(),
    ),
  );
  expect(link(dom, "client-support").classList.contains("hidden")).toBe(true);
  resolveMe(json(paid));
  await visible(dom, "client-whatsapp-link");
  await vi.waitFor(() =>
    expect(link(dom, "orders-rows").textContent).toContain("Nenhum pedido encontrado."),
  );
  expect(
    dom.window.fetch.mock.calls.filter(([url]) => url.endsWith("/auth/me")),
  ).toHaveLength(1);
});

it("refreshes the paid identity when returning to the client area", async () => {
  let identity = paid;
  const dom = open("client-area", {
    override: (path) => (path === "/auth/me" ? json(identity) : undefined),
  });
  await visible(dom, "client-whatsapp-link");
  await vi.waitFor(() =>
    expect(link(dom, "orders-rows").textContent).toContain("Nenhum pedido encontrado."),
  );
  identity = free;
  dom.window.dispatchEvent(new dom.window.Event("focus"));
  await vi.waitFor(() =>
    expect(link(dom, "plan-summary").textContent).toBe("Plano gratuito"),
  );
  expect(link(dom, "client-support").classList.contains("hidden")).toBe(true);
  expect(link(dom, "client-whatsapp-link").hasAttribute("href")).toBe(false);
  expect(
    dom.window.fetch.mock.calls.filter(([url]) => url.endsWith("/auth/me")),
  ).toHaveLength(2);
});

it.each(plans)(
  "provides checkout help for $name to free visitors without altering form values",
  async (plan) => {
    const dom = open("checkout", { me: free, query: `?plan=${plan.code}` });
    link(dom, "customer-name").value = "Nome preenchido";
    const url = await visible(dom, "checkout-whatsapp-link");
    await vi.waitFor(() =>
      expect(
        new URL(link(dom, "checkout-whatsapp-link").href).searchParams.get(
          "text",
        ),
      ).toBe(
        `Olá! Estou contratando o plano ${plan.name} do OFX Simples e preciso de ajuda com a contratação.`,
      ),
    );
    expect(url.hostname).toBe("wa.me");
    expect(link(dom, "customer-name").value).toBe("Nome preenchido");
    expect(link(dom, "checkout-whatsapp-link").target).toBe("_blank");
    expect(link(dom, "checkout-whatsapp-link").rel).toBe("noopener noreferrer");
  },
);

it("keeps generic checkout help available while the catalog is unavailable", async () => {
  const dom = open("checkout", {
    me: null,
    override: (path) => (path === "/plans" ? new Promise(() => {}) : undefined),
  });
  const url = await visible(dom, "checkout-whatsapp-link");
  expect(url.searchParams.get("text")).toBe(
    "Olá! Estou na página de contratação do OFX Simples e preciso de ajuda com a contratação.",
  );
});

it.each([null, "", "javascript:alert(1)"])(
  "hides links on all pages for invalid contact %j",
  async (value) => {
    for (const [page, id] of [
      ["client-area", "client-whatsapp-link"],
      ["checkout", "checkout-whatsapp-link"],
      ["planos", "plans-whatsapp-link"],
    ]) {
      const dom = open(page, { config: { whatsapp_support_number: value } });
      await vi.waitFor(() =>
        expect(dom.window.fetch).toHaveBeenCalledWith(
          expect.stringContaining("/public-config"),
          expect.anything(),
        ),
      );
      expect(link(dom, id).closest(".hidden")).not.toBeNull();
      expect(link(dom, id).hasAttribute("href")).toBe(false);
    }
  },
);

it("preserves the plans page message", async () => {
  const dom = open("planos");
  const url = await visible(dom, "plans-whatsapp-link");
  expect(url.searchParams.get("text")).toContain(
    "gostaria de tirar algumas dúvidas antes de escolher um plano",
  );
});

it("shows paid support when configuration arrives after the identity", async () => {
  let resolveConfig;
  const response = new Promise((resolve) => {
    resolveConfig = resolve;
  });
  const dom = open("client-area", {
    override: (path) => (path === "/public-config" ? response : undefined),
  });
  await vi.waitFor(() =>
    expect(link(dom, "profile-email").textContent).toBe(paid.email),
  );
  expect(link(dom, "client-support").classList.contains("hidden")).toBe(true);
  resolveConfig(json({ whatsapp_support_number: number }));
  await visible(dom, "client-whatsapp-link");
});

it.each(["client-area", "checkout"])(
  "keeps %s usable when public config fails",
  async (page) => {
    const dom = open(page, {
      override: (path) =>
        path === "/public-config" ? json({}, 503) : undefined,
    });
    await vi.waitFor(() =>
      expect(
        dom.window.fetch.mock.calls.filter(([url]) =>
          url.endsWith("/public-config"),
        ),
      ).toHaveLength(2),
    );
    const id =
      page === "checkout" ? "checkout-whatsapp-link" : "client-whatsapp-link";
    expect(link(dom, id).closest(".hidden")).not.toBeNull();
    expect(link(dom, id).hasAttribute("href")).toBe(false);
    if (page === "checkout") {
      await vi.waitFor(() =>
        expect(link(dom, "plan-name").textContent).toBe("Essencial"),
      );
      expect(link(dom, "checkout-submit").disabled).toBe(false);
    } else {
      await vi.waitFor(() =>
        expect(link(dom, "profile-email").textContent).toBe(paid.email),
      );
    }
  },
);
