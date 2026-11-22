"use client";

import { useEffect, useState } from "react";

interface Availability {
  expired: boolean;
  expiresAt?: string;
}

// Keep cached lists and open tabs consistent when a source expires locally.
export function useAvailableImports<T>(
  items: T[] | undefined,
  availability: (item: T) => Availability,
): T[] {
  const [now, setNow] = useState(() => Date.now());
  const available = (items ?? []).filter((item) => {
    const state = availability(item);
    const expiresAt = state.expiresAt ? Date.parse(state.expiresAt) : NaN;
    return !state.expired && !(expiresAt <= now);
  });
  const nextExpiry = Math.min(
    ...available
      .map((item) => Date.parse(availability(item).expiresAt ?? ""))
      .filter(Number.isFinite),
    Infinity,
  );

  useEffect(() => {
    const refresh = () => setNow(Date.now());
    const timer = Number.isFinite(nextExpiry)
      ? window.setTimeout(
          refresh,
          Math.max(0, Math.min(nextExpiry - Date.now(), 2147483647)),
        )
      : undefined;
    window.addEventListener("focus", refresh);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("focus", refresh);
    };
  }, [nextExpiry, now]);
  return available;
}

export const documentAvailability = (item: {
  status: string;
  is_expired: boolean;
  expires_at?: string;
}): Availability => ({
  expired: item.is_expired || item.status === "expired",
  expiresAt: item.expires_at,
});

export const batchAvailability = (item: {
  status: string;
  isExpired: boolean;
  expiresAt: string;
}): Availability => ({
  expired: item.isExpired || item.status === "expired",
  expiresAt: item.expiresAt,
});
