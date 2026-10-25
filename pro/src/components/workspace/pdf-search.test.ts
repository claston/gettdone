import { expect, it } from "vitest";
import type { PDFPageProxy } from "pdfjs-dist";
import type { TextContent, TextItem } from "pdfjs-dist/types/src/display/api";
import { findPdfPageMatches, indexPdfPage } from "./pdf-search";

const viewport = {
  width: 600,
  height: 800,
  convertToViewportPoint: (x: number, y: number) => [x, 800 - y],
} as ReturnType<PDFPageProxy["getViewport"]>;
const item = (str: string, x: number, y: number): TextItem => ({
  str,
  transform: [10, 0, 0, 10, x, y],
  width: 80,
  height: 10,
  fontName: "test",
  dir: "ltr",
  hasEOL: false,
});

it("localiza frases entre fragmentos, ignora acentos e conserva as caixas originais", () => {
  const content: TextContent = {
    items: [
      item("Crédito", 30, 600),
      item("recebido", 150, 600),
      item("Crédito recebido", 30, 550),
    ],
    styles: {},
    lang: null,
  };
  const page = indexPdfPage(2, content, viewport);
  const matches = findPdfPageMatches(page, "CREDITO recebido");
  expect(matches).toHaveLength(2);
  expect(matches[0].regions).toHaveLength(2);
  expect(matches[0].regions[0]).toMatchObject({
    page: 2,
    left: 0.05,
    top: 0.2375,
  });
  expect(matches[1].regions[0].top).toBeGreaterThan(matches[0].regions[0].top);
  expect(findPdfPageMatches(page, "ausente")).toEqual([]);
  expect(findPdfPageMatches(page, "")).toEqual([]);
});

it("respeita a transformação da página e não estima caixas para PDFs sem texto", () => {
  const rotated = {
    width: 800,
    height: 600,
    convertToViewportPoint: (x: number, y: number) => [y, x],
  } as ReturnType<PDFPageProxy["getViewport"]>;
  const page = indexPdfPage(
    1,
    { items: [item("PIX", 30, 600)], styles: {}, lang: null },
    rotated,
  );
  expect(findPdfPageMatches(page, "pix")[0].regions[0]).toMatchObject({
    left: 0.75,
    top: 0.05,
  });
  expect(
    indexPdfPage(1, { items: [], styles: {}, lang: null }, viewport).text,
  ).toBe("");
  expect(
    findPdfPageMatches({ ...page, text: "pix ".repeat(1000) }, "pix", 501),
  ).toHaveLength(501);
});

it("busca palavras divididas em fragmentos e nomes unidos pela extração", () => {
  const page = indexPdfPage(
    1,
    {
      items: [
        item("Per", 30, 700),
        item("í", 45, 700),
        item("odo", 48, 700),
        item("CLIENTE SINTÉTICO DE", 30, 600),
        item("TESTE", 30, 580),
      ],
      styles: {},
      lang: null,
    },
    viewport,
  );
  expect(findPdfPageMatches(page, "período")).toHaveLength(1);
  const [match] = findPdfPageMatches(page, "CLIENTE SINTETICO DETESTE");
  expect(match.regions).toHaveLength(2);
  expect(match.regions[0].top).toBeLessThan(match.regions[1].top);
});

it("destaca texto mesmo quando a fonte informa ascensão e descida iguais a zero", () => {
  const page = indexPdfPage(
    1,
    {
      items: [item("PIX RECEBIDO", 30, 600)],
      styles: {
        test: {
          ascent: 0,
          descent: 0,
          vertical: false,
          fontFamily: "sans-serif",
        },
      },
      lang: null,
    },
    viewport,
  );
  const [match] = findPdfPageMatches(page, "PIX RECEBIDO");
  expect(match.regions).toHaveLength(1);
  expect(match.regions[0].height).toBeGreaterThan(0);
  expect(match.regions[0].top).toBeLessThan((800 - 600) / 800);
});
