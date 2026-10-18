import { describe, expect, it, vi } from "vitest";

describe("PDF browser compatibility", () => {
  it("supports resolving and rejecting when withResolvers is absent", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(
      Promise,
      "withResolvers",
    );
    try {
      Object.defineProperty(Promise, "withResolvers", {
        configurable: true,
        writable: true,
        value: undefined,
      });
      vi.resetModules();
      await import("./pdf-browser-support");
      const success = Promise.withResolvers<number>();
      success.resolve(42);
      await expect(success.promise).resolves.toBe(42);
      const failure = Promise.withResolvers();
      failure.reject(new Error("synthetic"));
      await expect(failure.promise).rejects.toThrow("synthetic");
    } finally {
      if (descriptor)
        Object.defineProperty(Promise, "withResolvers", descriptor);
      else Reflect.deleteProperty(Promise, "withResolvers");
    }
  });
});
