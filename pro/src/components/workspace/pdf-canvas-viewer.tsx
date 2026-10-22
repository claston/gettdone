"use client";
import type {
  PdfNavigationRequest,
  PdfReadingPosition,
} from "./source-location";
import "./pdf-browser-support";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import {
  validSourceRegions,
  regionScrollPosition,
  pdfReadingPosition,
} from "./source-location";
import { PdfSourceHighlights } from "./pdf-source-highlights";
import { PdfInteractionLayer } from "./pdf-interaction-layer";
import type { PdfReviewControls } from "./pdf-interactions";
import { usePdfSearch } from "./use-pdf-search";
import { usePdfZoom } from "./use-pdf-zoom";
import type { PdfSearchMatch } from "./pdf-search";
import {
  activePageAt,
  pageLayout,
  readingAnchor,
  scrollForAnchor,
  type PageSize,
} from "./pdf-navigation";

pdfjs.GlobalWorkerOptions.workerSrc = `/pdfjs/${pdfjs.version}/pdf.worker.min.mjs`;
const options = {
  cMapUrl: `/pdfjs/${pdfjs.version}/cmaps/`,
  cMapPacked: true,
  standardFontDataUrl: `/pdfjs/${pdfjs.version}/standard_fonts/`,
  isEvalSupported: false,
};

export default function PdfCanvasViewer({
  url,
  blob,
  name,
  page,
  onPage,
  navigation,
  retry,
  onAvailability,
  onReadingPosition,
  review,
}: {
  url: string;
  blob: Blob;
  name: string;
  page: number;
  onPage(page: number): void;
  navigation: PdfNavigationRequest | null;
  retry(): void;
  onAvailability(available: boolean): void;
  onReadingPosition?(position: PdfReadingPosition): void;
  review?: PdfReviewControls;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  const [sizes, setSizes] = useState<PageSize[]>([]);
  const [error, setError] = useState(false);
  const [width, setWidth] = useState(600);
  const { zoom, increase, decrease, reset } = usePdfZoom(
    stage,
    sizes.length > 0 && !error,
  );
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const search = usePdfSearch(document);
  const appliedSearch = useRef<PdfSearchMatch | undefined>(undefined);
  const [viewport, setViewport] = useState({ top: 0, height: 800 });
  const boxes = useMemo(
    () => pageLayout(sizes, Math.max(100, width - 24) * zoom),
    [sizes, width, zoom],
  );
  const previousBoxes = useRef(boxes);
  const position = useRef(0);
  const userScrolling = useRef(false);
  const scrollFrame = useRef<number | null>(null);
  const appliedNavigation = useRef<PdfNavigationRequest | null>(null);
  const selectedRegions = useMemo(
    () => validSourceRegions(navigation?.regions, sizes.length),
    [navigation, sizes.length],
  );
  const selectedTransaction = review?.transactions.find(
    (row) => row.id === review.selectedId,
  );
  const initialPage = useRef(page);
  const onPageRef = useRef(onPage);
  const onReadingRef = useRef(onReadingPosition);
  useLayoutEffect(() => {
    onPageRef.current = onPage;
    onReadingRef.current = onReadingPosition;
  }, [onPage, onReadingPosition]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (scrollFrame.current !== null)
        cancelAnimationFrame(scrollFrame.current);
    };
  }, []);
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      setWidth(element.clientWidth);
      setViewport({ top: element.scrollTop, height: element.clientHeight });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    const element = stage.current;
    if (!element || !boxes.length) return;
    userScrolling.current = false;
    const previous = previousBoxes.current;
    const anchor = previous.length
      ? readingAnchor(previous, position.current)
      : {
          page: Math.min(boxes.length, Math.max(1, initialPage.current)),
          fraction: 0,
        };
    element.scrollTop = scrollForAnchor(boxes, anchor);
    position.current = element.scrollTop;
    if (!previous.length && initialPage.current !== anchor.page)
      onPageRef.current(anchor.page);
    previousBoxes.current = boxes;
    setViewport({ top: element.scrollTop, height: element.clientHeight });
  }, [boxes]);
  useLayoutEffect(() => {
    if (
      !navigation ||
      !boxes.length ||
      appliedNavigation.current === navigation ||
      !stage.current
    )
      return;
    // Clearing a selection can restart its token. Identity still distinguishes
    // a new request while resize/zoom reuse the already applied request.
    appliedNavigation.current = navigation;
    if (navigation.scroll === false) return;
    if (
      !Number.isInteger(navigation.page) ||
      navigation.page < 1 ||
      navigation.page > boxes.length
    )
      return;
    const box = boxes[navigation.page - 1];
    const region = selectedRegions.find(
      (item) => item.page === navigation.page,
    );
    userScrolling.current = false;
    const top = region
      ? regionScrollPosition(
          box,
          region,
          stage.current.clientHeight,
          navigation.origin === "grid",
        )
      : box.top;
    const smooth =
      navigation.origin === "grid" &&
      Math.abs(stage.current.scrollTop - top) <
        stage.current.clientHeight * 1.5 &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const left = region
      ? Math.max(
          0,
          (region.left + region.width / 2) * box.width -
            stage.current.clientWidth / 2,
        )
      : stage.current.scrollLeft;
    // Use one command so horizontal centering cannot cancel vertical animation.
    stage.current.scrollTo({ top, left, behavior: smooth ? "smooth" : "auto" });
    position.current = stage.current.scrollTop;
    setViewport({
      top: stage.current.scrollTop,
      height: stage.current.clientHeight,
    });
    onPageRef.current(navigation.page);
  }, [navigation, boxes, selectedRegions]);
  useLayoutEffect(() => {
    const match = search.activeMatch;
    const element = stage.current;
    if (
      !match ||
      !element ||
      !boxes[match.page - 1] ||
      appliedSearch.current === match
    )
      return;
    appliedSearch.current = match;
    userScrolling.current = false;
    const box = boxes[match.page - 1];
    const region = match.regions[0];
    element.scrollTo({
      top: region
        ? regionScrollPosition(box, region, element.clientHeight)
        : box.top,
      left: region
        ? Math.max(
            0,
            (region.left + region.width / 2) * box.width -
              element.clientWidth / 2,
          )
        : 0,
      behavior: "auto",
    });
    position.current = element.scrollTop;
    setViewport({ top: element.scrollTop, height: element.clientHeight });
    onPageRef.current(match.page);
  }, [search.activeMatch, boxes]);
  const fail = () => {
    if (alive.current) {
      setError(true);
      onAvailability(false);
    }
  };
  const loaded = async (pdf: PDFDocumentProxy) => {
    try {
      // Only dimensions are read here; canvases remain limited to the viewport.
      const dimensions: PageSize[] = [];
      for (let number = 1; number <= pdf.numPages; number++) {
        if (!alive.current) return;
        const pdfPage = await pdf.getPage(number);
        const view = pdfPage.getViewport({ scale: 1 });
        dimensions.push({ width: view.width, height: view.height });
      }
      if (alive.current) {
        setSizes(dimensions);
        setDocument(pdf);
        onAvailability(true);
      }
    } catch {
      fail();
    }
  };
  const go = (target: number) => {
    if (!stage.current || !boxes[target - 1]) return;
    userScrolling.current = false;
    stage.current.scrollTop = boxes[target - 1].top;
    position.current = stage.current.scrollTop;
    setViewport({
      top: stage.current.scrollTop,
      height: stage.current.clientHeight,
    });
    onPage(target);
    onReadingRef.current?.({ page: target, fraction: 0 });
  };
  const reportedPage = useRef(page);
  const onScroll = () => {
    const element = stage.current;
    if (!element || !boxes.length) return;
    position.current = element.scrollTop;
    setViewport({ top: element.scrollTop, height: element.clientHeight });
    if (!userScrolling.current || scrollFrame.current !== null) return;
    scrollFrame.current = requestAnimationFrame(() => {
      scrollFrame.current = null;
      if (!userScrolling.current || !alive.current) return;
      const active = activePageAt(
        boxes,
        element.scrollTop,
        element.clientHeight,
      );
      if (active !== reportedPage.current) {
        reportedPage.current = active;
        onPageRef.current(active);
      }
      onReadingRef.current?.(
        pdfReadingPosition(boxes, element.scrollTop, element.clientHeight),
      );
    });
  };
  const startUserScroll = () => {
    if (!userScrolling.current && stage.current)
      stage.current.scrollTo({
        top: stage.current.scrollTop,
        behavior: "auto",
      });
    userScrolling.current = true;
  };
  const stopMotion = () => {
    const element = stage.current;
    if (!element || !boxes.length) return;
    userScrolling.current = false;
    element.scrollTo({
      top: element.scrollTop,
      left: element.scrollLeft,
      behavior: "auto",
    });
    position.current = element.scrollTop;
    setViewport({ top: element.scrollTop, height: element.clientHeight });
    onPageRef.current(
      activePageAt(boxes, element.scrollTop, element.clientHeight),
    );
  };
  useLayoutEffect(() => {
    reportedPage.current = page;
  }, [page]);
  return (
    <>
      {navigation?.transactionId && sizes.length > 0 && !error && (
        <span className="sr-only" role="status">
          {selectedRegions.length
            ? "Origem do lançamento destacada no PDF."
            : "Página de origem aberta. A região exata não está disponível para este lançamento."}
        </span>
      )}
      <div className="panel-toolbar pdf-toolbar">
        <span className="toolbar-title" title={name}>
          {name}
        </span>
        <div className="pdf-controls">
          <button
            className="icon-button"
            aria-label="Página anterior"
            disabled={!sizes.length || page <= 1}
            onClick={() => go(page - 1)}
          >
            ‹
          </button>
          <span>
            Página <strong>{page}</strong> de {sizes.length || "…"}
          </span>
          <button
            className="icon-button"
            aria-label="Próxima página"
            disabled={!sizes.length || page >= sizes.length}
            onClick={() => go(page + 1)}
          >
            ›
          </button>
          <button
            className="icon-button"
            aria-label="Diminuir zoom"
            disabled={zoom <= 0.5}
            onClick={decrease}
            title="Diminuir zoom do PDF (Ctrl/Cmd + −)"
          >
            −
          </button>
          <button
            className="zoom-label"
            aria-label="Ajustar à largura"
            onClick={reset}
            title="Ajustar à largura (Ctrl/Cmd + 0)"
          >
            {Math.round(zoom * 100)}%
          </button>
          <button
            className="icon-button"
            aria-label="Aumentar zoom"
            disabled={zoom >= 2}
            onClick={increase}
            title="Aumentar zoom do PDF (Ctrl/Cmd + +). Ctrl/Cmd + roda do mouse também amplia o PDF."
          >
            +
          </button>
          <a
            className="icon-button"
            href={`${url}#page=${page}`}
            target="_blank"
            rel="noreferrer"
            aria-label="Abrir PDF original"
          >
            ↗
          </a>
        </div>
      </div>
      {!error && sizes.length > 0 && (
        <div className="pdf-search" role="search" aria-label="Busca no PDF">
          <input
            type="search"
            aria-label="Buscar no PDF"
            placeholder="Buscar no PDF"
            maxLength={120}
            value={search.query}
            onChange={(event) => search.setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                event.stopPropagation();
                search.next(event.shiftKey ? -1 : 1);
              }
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                search.setQuery("");
              }
            }}
          />
          <button
            className="icon-button"
            aria-label="Ocorrência anterior no PDF"
            disabled={!search.matches.length || search.busy}
            onClick={() => search.next(-1)}
          >
            ‹
          </button>
          <button
            className="icon-button"
            aria-label="Próxima ocorrência no PDF"
            disabled={!search.matches.length || search.busy}
            onClick={() => search.next(1)}
          >
            ›
          </button>
          <span role="status">
            {search.busy
              ? "Buscando…"
              : search.query.trim()
                ? search.error
                  ? "Não foi possível buscar."
                  : search.matches.length
                    ? `${search.index + 1} de ${search.matches.length}${search.truncated ? "+" : ""}`
                    : search.hasText
                      ? "Nenhuma ocorrência."
                      : "PDF sem texto pesquisável."
                : ""}
          </span>
          {search.error && (
            <button className="secondary-button small" onClick={search.retry}>
              Tentar busca novamente
            </button>
          )}
        </div>
      )}
      {review && !error && sizes.length > 0 && (
        <div className="pdf-mark-toolbar">
          <button
            type="button"
            className="secondary-button small"
            disabled={!selectedTransaction || selectedTransaction.isDeleted}
            aria-pressed={Boolean(review.drawingFor)}
            onClick={
              review.drawingFor
                ? review.onCancelDrawing
                : () => {
                    stopMotion();
                    review.onStartDrawing();
                  }
            }
          >
            {review.drawingFor ? "Cancelar marcação" : "Marcar no PDF"}
          </button>
          {Boolean(selectedTransaction?.reviewRegions?.length) && (
            <button
              type="button"
              className="secondary-button small"
              disabled={review.paused || Boolean(review.drawingFor)}
              onClick={review.onClearRegions}
            >
              Limpar marcações
            </button>
          )}
          <span
            className="pdf-mark-label"
            title={selectedTransaction?.description}
          >
            {review.drawingFor
              ? `Desenhe um retângulo para ${review.drawingFor.description}. Esc cancela.`
              : selectedTransaction
                ? selectedTransaction.description
                : "Selecione um lançamento para marcar no PDF"}
          </span>
        </div>
      )}
      <div
        className="pdf-stage pdf-continuous"
        ref={stage}
        onScroll={onScroll}
        onWheel={(event) => {
          if (!event.ctrlKey && !event.metaKey) startUserScroll();
        }}
        onTouchStart={startUserScroll}
        onPointerDown={(event) => {
          if (
            event.pointerType !== "touch" &&
            !(event.target as HTMLElement).closest(
              "button, a, input, textarea, select",
            )
          )
            event.currentTarget.focus({ preventScroll: true });
          if (
            event.pointerType === "touch" ||
            event.target === event.currentTarget
          )
            startUserScroll();
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && review?.drawingFor) {
            event.preventDefault();
            review.onCancelDrawing();
            return;
          }
          if (
            [
              "ArrowDown",
              "ArrowUp",
              "PageDown",
              "PageUp",
              "Home",
              "End",
              " ",
            ].includes(event.key)
          )
            startUserScroll();
        }}
        tabIndex={0}
        role="region"
        aria-label="Visualizador do extrato original"
      >
        {error ? (
          <div className="viewer-state" role="alert">
            <p>
              Não foi possível exibir o PDF. Suas edições continuam disponíveis.
            </p>
            <button className="secondary-button" onClick={retry}>
              Recarregar PDF
            </button>
          </div>
        ) : (
          <Document
            file={blob}
            options={options}
            onLoadSuccess={loaded}
            onLoadError={fail}
            onSourceError={fail}
            loading={<p role="status">Carregando páginas…</p>}
          >
            {!boxes.length && <p role="status">Preparando páginas…</p>}
            {boxes.map((box, index) => {
              const near =
                box.top + box.height >= viewport.top - viewport.height &&
                box.top <= viewport.top + viewport.height * 2;
              return (
                <div
                  key={index + 1}
                  className="pdf-page-slot"
                  data-pdf-page={index + 1}
                  aria-label={`Página ${index + 1}`}
                  style={{ width: box.width, height: box.height }}
                >
                  {near ? (
                    <Page
                      pageNumber={index + 1}
                      width={box.width}
                      devicePixelRatio={Math.min(
                        window.devicePixelRatio || 1,
                        1.5,
                      )}
                      renderTextLayer={false}
                      renderAnnotationLayer={false}
                      onRenderError={fail}
                      onLoadError={fail}
                      loading={<span>Carregando página {index + 1}…</span>}
                    />
                  ) : (
                    <span className="pdf-placeholder">Página {index + 1}</span>
                  )}
                  {near && review && (
                    <PdfInteractionLayer
                      key={review.drawingFor?.id ?? "browse"}
                      page={index + 1}
                      review={review}
                      onGestureStart={stopMotion}
                    />
                  )}
                  {near && (
                    <PdfSourceHighlights
                      regions={selectedRegions.filter(
                        (region) => region.page === index + 1,
                      )}
                      transactionId={navigation?.transactionId}
                      manualRegions={selectedTransaction?.reviewRegions}
                    />
                  )}
                  {near &&
                    !search.busy &&
                    search.matches
                      .filter((match) => match.page === index + 1)
                      .flatMap((match, occurrence) =>
                        match.regions.map((region, fragment) => (
                          <div
                            key={`search:${occurrence}:${fragment}`}
                            className={`pdf-search-highlight ${match === search.activeMatch ? "is-active" : ""}`}
                            aria-hidden="true"
                            style={{
                              left: `${region.left * 100}%`,
                              top: `${region.top * 100}%`,
                              width: `${region.width * 100}%`,
                              height: `${region.height * 100}%`,
                            }}
                          />
                        )),
                      )}
                </div>
              );
            })}
          </Document>
        )}
      </div>
    </>
  );
}
