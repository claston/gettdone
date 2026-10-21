import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { usePdfSearch } from "./use-pdf-search";

afterEach(() => vi.useRealTimers());
const page = (text: string) => ({
  getTextContent: vi
    .fn()
    .mockResolvedValue({
      items: text
        ? [
            {
              str: text,
              transform: [10, 0, 0, 10, 20, 40],
              width: 100,
              height: 10,
              fontName: "a",
            },
          ]
        : [],
      styles: {},
    }),
  getViewport: () => ({
    width: 600,
    height: 800,
    convertToViewportPoint: (x: number, y: number) => [x, 800 - y],
  }),
});

it("cancela a busca antiga, reutiliza o índice e limpa os destaques imediatamente ao apagar", async () => {
  vi.useFakeTimers();
  let finish!: (value: unknown) => void;
  const loaded = page("PIX e tarifa PIX");
  loaded.getTextContent.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const getPage = vi.fn().mockResolvedValue(loaded);
  const pdf = { numPages: 1, getPage } as unknown as PDFDocumentProxy;
  const { result } = renderHook(() => usePdfSearch(pdf));
  act(() => result.current.setQuery("tarifa"));
  await act(async () => vi.advanceTimersByTimeAsync(201));
  act(() => result.current.setQuery("PIX"));
  await act(async () => {
    finish(await page("PIX e tarifa PIX").getTextContent());
    await vi.advanceTimersByTimeAsync(201);
  });
  expect(result.current.busy).toBe(false);
  expect(result.current.matches).toHaveLength(2);
  act(() => result.current.next(-1));
  expect(result.current.index).toBe(1);
  expect(getPage).toHaveBeenCalledOnce();
  act(() => result.current.setQuery(""));
  expect(result.current.matches).toEqual([]);
  expect(result.current.activeMatch).toBeUndefined();
});

it("identifica PDF sem texto e troca o cache ao mudar de documento", async () => {
  vi.useFakeTimers();
  const empty = {
    numPages: 1,
    getPage: vi.fn().mockResolvedValue(page("")),
  } as unknown as PDFDocumentProxy;
  const next = {
    numPages: 1,
    getPage: vi.fn().mockResolvedValue(page("PIX")),
  } as unknown as PDFDocumentProxy;
  const { result, rerender } = renderHook(({ pdf }) => usePdfSearch(pdf), {
    initialProps: { pdf: empty },
  });
  act(() => result.current.setQuery("PIX"));
  await act(async () => vi.advanceTimersByTimeAsync(201));
  expect(result.current.hasText).toBe(false);
  expect(result.current.busy).toBe(false);
  rerender({ pdf: next });
  expect(result.current.matches).toEqual([]);
  await act(async () => vi.advanceTimersByTimeAsync(201));
  expect(result.current.matches).toHaveLength(1);
});
