"use client";

import "./workspace/pdf-browser-support";
import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/TextLayer.css";
import styles from "./identity-document-preview.module.css";

pdfjs.GlobalWorkerOptions.workerSrc = `/pdfjs/${pdfjs.version}/pdf.worker.min.mjs`;
const options = {
  cMapUrl: `/pdfjs/${pdfjs.version}/cmaps/`,
  cMapPacked: true,
  standardFontDataUrl: `/pdfjs/${pdfjs.version}/standard_fonts/`,
  isEvalSupported: false,
};

export default function SimplePdfViewer({
  blob,
  initialPage,
  reload,
}: {
  blob: Blob;
  initialPage: number;
  reload(): void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(320);
  const [pages, setPages] = useState(0);
  const [page, setPage] = useState(initialPage);
  const [zoom, setZoom] = useState(1);
  const [failed, setFailed] = useState(false);
  const [text, setText] = useState<{ page: number; empty: boolean } | null>(
    null,
  );
  useEffect(() => {
    if (!viewport.current) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(160, entry.contentRect.width - 16));
    });
    observer.observe(viewport.current);
    return () => observer.disconnect();
  }, []);
  const changePage = (next: number) => {
    setPage(Math.max(1, Math.min(pages, next)));
    viewport.current?.scrollTo({ top: 0, left: 0 });
  };
  const fail = () => setFailed(true);
  return (
    <>
      <p className={styles.hint}>
        Selecione e copie o texto do PDF para preencher a identificação.
      </p>
      <div
        className={styles.controls}
        role="group"
        aria-label="Controles do PDF"
      >
        <button
          type="button"
          className="secondary-button small"
          aria-label="Página anterior do PDF"
          disabled={!pages || page <= 1 || failed}
          onClick={() => changePage(page - 1)}
        >
          Anterior
        </button>
        <span aria-live="polite">
          {pages ? `Página ${page} de ${pages}` : "Carregando páginas…"}
        </span>
        <button
          type="button"
          className="secondary-button small"
          aria-label="Próxima página do PDF"
          disabled={!pages || page >= pages || failed}
          onClick={() => changePage(page + 1)}
        >
          Próxima
        </button>
        <button
          type="button"
          className="secondary-button small"
          aria-label="Diminuir zoom do PDF"
          disabled={zoom <= 0.75 || failed}
          onClick={() => setZoom((value) => value - 0.25)}
        >
          −
        </button>
        <span>{Math.round(zoom * 100)}%</span>
        <button
          type="button"
          className="secondary-button small"
          aria-label="Aumentar zoom do PDF"
          disabled={zoom >= 2.5 || failed}
          onClick={() => setZoom((value) => value + 0.25)}
        >
          +
        </button>
        <button
          type="button"
          className="secondary-button small"
          disabled={failed}
          onClick={() => setZoom(1)}
        >
          Ajustar à largura
        </button>
      </div>
      <div
        ref={viewport}
        className={styles.viewport}
        tabIndex={0}
        aria-label="Páginas do documento"
      >
        {failed ? (
          <div role="alert" className={styles.message}>
            <p>
              Não foi possível exibir este PDF. Os dados preenchidos continuam
              na tela.
            </p>
            <button type="button" className="secondary-button" onClick={reload}>
              Recarregar PDF
            </button>
          </div>
        ) : (
          <Document
            file={blob}
            options={options}
            onLoadSuccess={({ numPages }) => {
              setPages(numPages);
              setPage((value) => Math.max(1, Math.min(numPages, value)));
            }}
            onLoadError={fail}
            onSourceError={fail}
            loading={<p role="status">Carregando páginas…</p>}
          >
            {pages > 0 && (
              <Page
                key={page}
                pageNumber={page}
                width={width * zoom}
                devicePixelRatio={Math.min(window.devicePixelRatio || 1, 1.5)}
                renderTextLayer
                renderAnnotationLayer={false}
                onLoadError={fail}
                onRenderError={fail}
                onGetTextSuccess={(content) =>
                  setText({
                    page,
                    empty: !content.items.some(
                      (item) => "str" in item && item.str.trim(),
                    ),
                  })
                }
                loading={<p role="status">Carregando página {page}…</p>}
              />
            )}
          </Document>
        )}
      </div>
      {text?.page === page && text.empty && (
        <p role="status" className={styles.hint}>
          Esta página não tem texto selecionável. Confira os dados visualmente e
          preencha os campos.
        </p>
      )}
    </>
  );
}
