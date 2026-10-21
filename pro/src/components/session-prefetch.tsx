"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { batchHistoryService } from "@/data/batch-history-service";

export function SessionPrefetch() {
  const pathname = usePathname();
  const pending = useRef(false);
  useEffect(() => {
    if (
      pathname === "/dashboard" ||
      pending.current ||
      batchHistoryService.getCachedPage?.()
    )
      return;
    pending.current = true;
    void batchHistoryService
      .list()
      .catch(() => {})
      .finally(() => {
        pending.current = false;
      });
  }, [pathname]);
  return null;
}
