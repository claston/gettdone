import { expect, it, vi } from "vitest";
import { createHttpWorkspaceRepository } from "./http-workspace-repository";
import { invalidateProSessionCaches } from "./pro-session-cache";

const payload = (name = "Original", cursor?: string) => ({
  batch_id: "batch",
  client_name: name,
  jobs: [],
  next_cursor: cursor,
});
const json = (body: unknown, etag?: string) =>
  new Response(JSON.stringify(body), {
    headers: etag ? { ETag: etag } : {},
  });

it("reuses the complete workspace on 304 and adopts a remote change on 200", async () => {
  const responses = [
    json(payload(), 'W/"v1"'),
    new Response(null, { status: 304 }),
    json(payload("Remote"), 'W/"v2"'),
  ];
  const fetcher = vi.fn<typeof fetch>(async (url) =>
    String(url).includes("conversion-batches") ? responses.shift()! : json({}),
  );
  const repo = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  const first = await repo.getBatch("batch");
  expect(await repo.getBatch("batch")).toBe(first);
  expect((await repo.getBatch("batch")).clientName).toBe("Remote");
  const reads = fetcher.mock.calls.filter(([url]) =>
    String(url).includes("conversion-batches"),
  );
  expect(new Headers(reads[1][1]?.headers).get("If-None-Match")).toBe('W/"v1"');
});

it("validates all client pages once, then skips pagination when unchanged", async () => {
  let reads = 0;
  const fetcher = vi.fn<typeof fetch>(async (url) => {
    if (!String(url).includes("/workspace")) return json({});
    reads++;
    return reads === 1
      ? json(payload("Client", "next"), 'W/"all-pages"')
      : reads === 2
        ? json(payload("Client"))
        : new Response(null, { status: 304 });
  });
  const repo = createHttpWorkspaceRepository({
    apiUrl: "/",
    fetcher,
    clientId: "client",
  });
  const first = await repo.getBatch("client");
  expect(await repo.getBatch("client")).toBe(first);
  expect(reads).toBe(3);
});

it("discards validators on session changes and tolerates older servers", async () => {
  const tags: (string | null)[] = [];
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (!String(url).includes("conversion-batches")) return json({});
    tags.push(new Headers(init?.headers).get("If-None-Match"));
    return json(payload(), tags.length === 1 ? 'W/"v1"' : undefined);
  });
  const repo = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  await repo.getBatch("batch");
  invalidateProSessionCaches();
  await repo.getBatch("batch");
  await repo.getBatch("batch");
  expect(tags).toEqual([null, null, null]);
});

it("does not reuse a conditional response after the session changes in flight", async () => {
  let reads = 0;
  const fetcher = vi.fn<typeof fetch>(async (url) => {
    if (!String(url).includes("conversion-batches")) return json({});
    if (++reads === 1) return json(payload(), 'W/"v1"');
    invalidateProSessionCaches();
    return new Response(null, { status: 304 });
  });
  const repo = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  await repo.getBatch("batch");
  await expect(repo.getBatch("batch")).rejects.toMatchObject({
    name: "AbortError",
  });
  expect(repo.getCachedBatch?.("batch")).toBeUndefined();
});

it("does not retain a validator when a later page fails", async () => {
  const tags: (string | null)[] = [];
  let fail = true;
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (!String(url).includes("/workspace")) return json({});
    if (String(url).includes("cursor="))
      return fail ? new Response(null, { status: 503 }) : json(payload());
    tags.push(new Headers(init?.headers).get("If-None-Match"));
    return json(payload("Client", "next"), 'W/"v1"');
  });
  const repo = createHttpWorkspaceRepository({
    apiUrl: "/",
    fetcher,
    clientId: "client",
  });
  await expect(repo.getBatch("client")).rejects.toThrow("503");
  fail = false;
  await repo.getBatch("client");
  expect(tags).toEqual([null, null]);
});
