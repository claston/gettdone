import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import {
  documentAvailability,
  useAvailableImports,
} from "./use-available-imports";

it("oculta expirados em cache e retira os demais ao vencerem, sem recarregar a página", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-22T12:00:00Z"));
  const items = [
    { id: "flag", status: "completed", is_expired: true },
    { id: "status", status: "expired", is_expired: false },
    {
      id: "past",
      status: "completed",
      is_expired: false,
      expires_at: "2026-11-22T11:00:00Z",
    },
    {
      id: "live",
      status: "failed",
      is_expired: false,
      expires_at: "2026-11-22T12:00:05Z",
    },
    { id: "old-api", status: "running", is_expired: false },
  ];
  const { result, unmount } = renderHook(() =>
    useAvailableImports(items, documentAvailability),
  );
  try {
    expect(result.current.map((item) => item.id)).toEqual(["live", "old-api"]);
    act(() => vi.advanceTimersByTime(5000));
    expect(result.current.map((item) => item.id)).toEqual(["old-api"]);
    expect(items).toHaveLength(5);
  } finally {
    unmount();
    vi.useRealTimers();
  }
});
