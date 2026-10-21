import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkspaceRepository } from "@/domain/workspace";
import { useAuthorizedDocument } from "./use-authorized-document";

afterEach(() => vi.unstubAllGlobals());
describe("authorized PDF lifecycle", () => {
  it("aborts on document switch and ignores stale responses, revoking the loaded URL", async () => {
    let finish!: (blob: Blob) => void;
    const create = vi.fn(() => "blob:new");
    const revoke = vi.fn();
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL = create;
        static revokeObjectURL = revoke;
      },
    );
    const loadDocument = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Blob>((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValue(new Blob(["new"]));
    const repository = { loadDocument } as unknown as WorkspaceRepository;
    const { result, rerender, unmount } = renderHook(
      ({ id }) => useAuthorizedDocument(id, repository, 0),
      { initialProps: { id: "old" } },
    );
    const signal = loadDocument.mock.calls[0][1] as AbortSignal;
    rerender({ id: "new" });
    expect(signal.aborted).toBe(true);
    await waitFor(() => expect(result.current.url).toBe("blob:new"));
    expect(result.current.blob).toBeInstanceOf(Blob);
    await act(async () => finish(new Blob(["old"])));
    expect(create).toHaveBeenCalledTimes(1);
    unmount();
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:new");
  });
});
