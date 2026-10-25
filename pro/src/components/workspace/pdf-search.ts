import type { PDFPageProxy } from "pdfjs-dist";
import type { TextContent, TextItem } from "pdfjs-dist/types/src/display/api";
import type { SourceRegion } from "@/domain/workspace";
import { validSourceRegions } from "@/domain/source-regions";

export interface PdfSearchMatch {
  page: number;
  regions: SourceRegion[];
}
export interface PdfSearchPage {
  page: number;
  text: string;
  spans: { start: number; end: number; region?: SourceRegion }[];
}
export const normalizePdfQuery = (value: string) =>
  value
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLocaleLowerCase("pt-BR")
    .replace(/\s+/g, " ")
    .trim();

// PDF font runs can split a word; layout extraction can also join wrapped
// names. Compare characters while keeping each fragment's original geometry.
const searchableText = (value: string) =>
  normalizePdfQuery(value).replace(/\s/g, "");

export function indexPdfPage(
  page: number,
  content: TextContent,
  viewport: ReturnType<PDFPageProxy["getViewport"]>,
): PdfSearchPage {
  let text = "";
  const spans: PdfSearchPage["spans"] = [];
  for (const item of content.items) {
    if (!("str" in item)) continue;
    const normalized = searchableText(item.str);
    if (!normalized) continue;
    const start = text.length;
    text += normalized;
    spans.push({
      start,
      end: text.length,
      region: itemRegion(page, item, content, viewport),
    });
  }
  return { page, text, spans };
}

function itemRegion(
  page: number,
  item: TextItem,
  content: TextContent,
  viewport: ReturnType<PDFPageProxy["getViewport"]>,
): SourceRegion | undefined {
  const [a, b, c, d, e, f] = item.transform as number[];
  const horizontal = Math.hypot(a, b),
    vertical = Math.hypot(c, d);
  if (!horizontal || !vertical || !item.width || !item.height) return;
  const font = content.styles[item.fontName];
  let ascent = font?.ascent ?? 1;
  let descent = font?.descent ?? 0;
  // Some embedded fonts report both metrics as zero. The text item's own
  // position and height still provide geometry for the search highlight.
  if (
    !Number.isFinite(ascent) ||
    !Number.isFinite(descent) ||
    ascent <= descent
  ) {
    ascent = 1;
    descent = 0;
  }
  const points = [0, 1].flatMap((x) =>
    [descent, ascent].map((y) =>
      viewport.convertToViewportPoint(
        e +
          (x * item.width * a) / horizontal +
          (y * item.height * c) / vertical,
        f +
          (x * item.width * b) / horizontal +
          (y * item.height * d) / vertical,
      ),
    ),
  );
  const left = Math.max(
    0,
    Math.min(...points.map((p) => p[0])) / viewport.width,
  );
  const top = Math.max(
    0,
    Math.min(...points.map((p) => p[1])) / viewport.height,
  );
  const right = Math.min(
    1,
    Math.max(...points.map((p) => p[0])) / viewport.width,
  );
  const bottom = Math.min(
    1,
    Math.max(...points.map((p) => p[1])) / viewport.height,
  );
  return validSourceRegions([
    { page, left, top, width: right - left, height: bottom - top },
  ])[0];
}

export function findPdfPageMatches(
  page: PdfSearchPage,
  query: string,
  limit = 500,
): PdfSearchMatch[] {
  const needle = searchableText(query);
  if (!needle) return [];
  const matches: PdfSearchMatch[] = [];
  let start = 0;
  while (matches.length < limit) {
    const index = page.text.indexOf(needle, start);
    if (index < 0) break;
    const regions = page.spans
      .filter((span) => span.start < index + needle.length && span.end > index)
      .flatMap((span) => (span.region ? [span.region] : []));
    matches.push({ page: page.page, regions });
    start = index + needle.length;
  }
  return matches;
}
