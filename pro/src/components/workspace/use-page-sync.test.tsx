import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { usePageSync } from "./use-page-sync";

describe("page synchronization", () => {
  it("follows the previous page again after visiting an unmatched page", () => {
    const follow = vi.fn();
    const { rerender } = renderHook(
      ({ page, id }) => usePageSync(page, id, follow),
      { initialProps: { page: 1, id: "a" as string | undefined } },
    );
    rerender({ page: 2, id: undefined });
    rerender({ page: 1, id: "a" });
    expect(follow).toHaveBeenCalledTimes(2);
  });
  it("follows pages once, pauses during editing and resumes at the latest page", () => {
    const follow = vi.fn();
    const { result, rerender } = renderHook(
      ({ page, id }) => usePageSync(page, id, follow),
      { initialProps: { page: 1, id: "row-1" } },
    );
    expect(follow).toHaveBeenCalledWith("row-1");
    act(() => result.current.setEditing(true));
    expect(result.current.editing).toBe(true);
    rerender({ page: 2, id: "row-2" });
    expect(follow).toHaveBeenCalledTimes(1);
    act(() => result.current.setEditing(false));
    expect(follow).toHaveBeenLastCalledWith("row-2");
    rerender({ page: 2, id: "row-2" });
    expect(follow).toHaveBeenCalledTimes(2);
  });
  it("ignores unmatched and filtered pages and keeps the latest pending target", () => {
    const follow = vi.fn();
    const { result, rerender } = renderHook(
      ({ page, id }) => usePageSync(page, id, follow),
      { initialProps: { page: 1, id: "a" as string | undefined } },
    );
    rerender({ page: 2, id: undefined });
    expect(follow).toHaveBeenCalledTimes(1);
    act(() => result.current.setEditing(true));
    rerender({ page: 3, id: "c" });
    rerender({ page: 4, id: "d" });
    expect(follow).toHaveBeenCalledTimes(1);
    act(() => result.current.setEditing(false));
    expect(follow).toHaveBeenLastCalledWith("d");
    rerender({ page: 4, id: "d" });
    expect(follow).toHaveBeenCalledTimes(2);
  });
});
