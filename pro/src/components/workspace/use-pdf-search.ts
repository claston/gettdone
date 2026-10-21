"use client";
import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import {
  findPdfPageMatches,
  indexPdfPage,
  normalizePdfQuery,
  type PdfSearchMatch,
  type PdfSearchPage,
} from "./pdf-search";

export function usePdfSearch(pdf: PDFDocumentProxy | null) {
  const [query, setQuery] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [index, setIndex] = useState(0);
  const [state, setState] = useState({
    pdf: null as PDFDocumentProxy | null,
    needle: "",
    attempt: 0,
    matches: [] as PdfSearchMatch[],
    truncated: false,
    hasText: false,
    error: false,
  });
  const needle = normalizePdfQuery(query);
  const current =
    state.pdf === pdf && state.needle === needle && state.attempt === attempt;
  const enabled = !!pdf && !!needle;
  const matches = enabled && current ? state.matches : [];
  const cache = useRef(new Map<number, Promise<PdfSearchPage>>());
  useEffect(() => {
    cache.current.clear();
  }, [pdf]);
  useEffect(() => {
    let cancelled = false;
    if (!pdf || !needle) return;
    const timer = setTimeout(async () => {
      const matches: PdfSearchMatch[] = [];
      let hasText = false;
      try {
        for (let page = 1; page <= pdf.numPages; page++) {
          if (cancelled) return;
          let data = cache.current.get(page);
          if (!data) {
            data = pdf
              .getPage(page)
              .then(async (loaded) =>
                indexPdfPage(
                  page,
                  await loaded.getTextContent(),
                  loaded.getViewport({ scale: 1 }),
                ),
              );
            cache.current.set(page, data);
          }
          const indexed = await data;
          if (cancelled) return;
          hasText ||= !!indexed.text;
          matches.push(
            ...findPdfPageMatches(indexed, needle, 501 - matches.length),
          );
          if (matches.length > 500) break;
        }
        if (!cancelled) {
          setIndex(0);
          setState({
            pdf,
            needle,
            attempt,
            matches: matches.slice(0, 500),
            hasText,
            truncated: matches.length > 500,
            error: false,
          });
        }
      } catch {
        if (!cancelled)
          setState({
            pdf,
            needle,
            attempt,
            matches: [],
            hasText,
            truncated: false,
            error: true,
          });
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pdf, needle, attempt]);
  return {
    matches,
    busy: enabled && !current,
    truncated: current && state.truncated,
    hasText: current && state.hasText,
    error: current && state.error,
    query,
    setQuery,
    index,
    activeMatch: matches[index],
    next: (direction: number) =>
      setIndex((value) =>
        matches.length
          ? (value + direction + matches.length) % matches.length
          : 0,
      ),
    retry: () => {
      cache.current.clear();
      setAttempt((value) => value + 1);
    },
  };
}
