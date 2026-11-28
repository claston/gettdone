"use client";

import Script from "next/script";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

declare global {
  interface Window {
    OfxAnalytics?: { capture(event: string): void; pageView(): void };
  }
}

export function ProductAnalytics() {
  const pathname = usePathname();
  useEffect(() => {
    window.OfxAnalytics?.pageView();
  }, [pathname]);
  return <Script src="/privacy-consent.js?v=3" strategy="afterInteractive" />;
}
