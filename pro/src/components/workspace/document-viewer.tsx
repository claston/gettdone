"use client";
import type {
  PdfNavigationRequest,
  PdfReadingPosition,
} from "./source-location";
import { useState } from "react";
import {
  FileText,
  XCircle,
  LoaderCircle,
  ChevronLeft,
  ChevronRight,
  Minus,
  Plus,
  ExternalLink,
} from "lucide-react";
import type { Conversion, WorkspaceRepository } from "@/domain/workspace";
import { RealDocumentViewer } from "./real-document-viewer";
import type { PdfReviewControls } from "./pdf-interactions";

export function DocumentViewer({
  conversion,
  repository,
  page,
  setPage,
  navigation,
  onAvailability,
  onReadingPosition,
  review,
}: {
  conversion: Conversion;
  repository: WorkspaceRepository;
  page: number;
  setPage(page: number): void;
  navigation: PdfNavigationRequest | null;
  onAvailability(available: boolean): void;
  onReadingPosition?(position: PdfReadingPosition): void;
  review?: PdfReviewControls;
}) {
  const [zoom, setZoom] = useState<"page-width" | number>("page-width");
  const isDemo = conversion.pdf?.url.startsWith("/mock-statement");
  if (conversion.pdf && !isDemo && conversion.status !== "FAILED") {
    return (
      <RealDocumentViewer
        conversion={conversion}
        repository={repository}
        page={page}
        setPage={setPage}
        navigation={navigation}
        onAvailability={onAvailability}
        onReadingPosition={onReadingPosition}
        review={review}
      />
    );
  }
  const format = conversion.fileName.split(".").pop()?.toUpperCase();
  if (!conversion.pdf && conversion.status !== "FAILED" && format !== "PDF") {
    return (
      <section
        className="pdf-panel viewer-state"
        aria-label="Documento original"
      >
        <FileText size={30} />
        <h3>Arquivo original em {format || "formato de dados"}</h3>
        <p>Este formato não possui visualização em PDF.</p>
        <p>
          {conversion.status === "PROCESSING"
            ? "Os lançamentos aparecerão ao lado quando o processamento terminar."
            : "Os lançamentos extraídos estão disponíveis ao lado para revisão."}
        </p>
      </section>
    );
  }

  if (!conversion.pdf || conversion.status === "FAILED") {
    return (
      <div className="viewer-state error">
        <XCircle size={30} />
        <h3>Não foi possível processar o arquivo</h3>
        <p>O restante do lote continua disponível.</p>
      </div>
    );
  }

  const previewRows = [
    ["02/09/2026", "PIX recebido — Comércio Horizonte", "145,90", "38.566,07"],
    ["03/09/2026", "Pagamento de boleto", "− 283,13", "38.282,94"],
    ["04/09/2026", "Transferência entre contas", "− 420,36", "37.862,58"],
    ["05/09/2026", "Tarifa pacote de serviços", "− 557,59", "37.304,99"],
    ["06/09/2026", "Recebimento via cartão", "694,82", "37.999,81"],
    ["09/09/2026", "PIX enviado — Fornecedor Delta", "− 832,05", "37.167,76"],
    ["10/09/2026", "Aplicação automática", "− 969,28", "36.198,48"],
    ["11/09/2026", "Resgate de investimento", "1.106,51", "37.304,99"],
    ["12/09/2026", "TED recebida", "1.243,74", "38.548,73"],
  ];
  return (
    <section className="pdf-panel" aria-label="Documento original">
      <div className="panel-toolbar">
        <div className="toolbar-title">
          <FileText size={16} />
          <span>{conversion.fileName}</span>
          {conversion.status === "PROCESSING" && (
            <span className="pdf-processing-status" role="status">
              <LoaderCircle className="spin" size={12} /> Processando dados
            </span>
          )}
        </div>
        <div className="pdf-controls">
          <button
            className="icon-button"
            onClick={() => setPage(Math.max(1, page - 1))}
            disabled={page === 1}
            aria-label="Página anterior"
          >
            <ChevronLeft size={17} />
          </button>
          <span>
            Página <strong>{page}</strong> de {conversion.pdf.pageCount}
          </span>
          <button
            className="icon-button"
            onClick={() =>
              setPage(Math.min(conversion.pdf!.pageCount, page + 1))
            }
            disabled={page === conversion.pdf.pageCount}
            aria-label="Próxima página"
          >
            <ChevronRight size={17} />
          </button>
          <span className="toolbar-divider" />
          <button
            className="icon-button"
            onClick={() =>
              setZoom((value) =>
                Math.max(70, (typeof value === "number" ? value : 100) - 10),
              )
            }
            aria-label="Diminuir zoom"
          >
            <Minus size={16} />
          </button>
          <button className="zoom-label" onClick={() => setZoom("page-width")}>
            {zoom === "page-width" ? "Ajustar" : `${zoom}%`}
          </button>
          <button
            className="icon-button"
            onClick={() =>
              setZoom((value) =>
                Math.min(160, (typeof value === "number" ? value : 100) + 10),
              )
            }
            aria-label="Aumentar zoom"
          >
            <Plus size={16} />
          </button>
          {isDemo && (
            <a
              className="icon-button"
              href={`${conversion.pdf.url}#page=${page}`}
              target="_blank"
              rel="noreferrer"
              aria-label="Abrir PDF original"
            >
              <ExternalLink size={15} />
            </a>
          )}
        </div>
      </div>
      <div className="pdf-stage">
        <div
          className="pdf-document"
          style={{ width: zoom === "page-width" ? "100%" : `${zoom}%` }}
          role="document"
          aria-label="Visualizador do extrato original"
        >
          {conversion.pdf.url.startsWith("/mock-statement") ? (
            <>
              <div className="mock-pdf-brand">
                <strong>{conversion.bankName}</strong>
                <span>Extrato de conta corrente</span>
              </div>
              <div className="mock-pdf-summary">
                <div>
                  <small>Agência</small>
                  <strong>0192</strong>
                </div>
                <div>
                  <small>Conta</small>
                  <strong>45.810-7</strong>
                </div>
                <div>
                  <small>Período</small>
                  <strong>01/09/2026–30/09/2026</strong>
                </div>
              </div>
              <h3>Movimentação da conta</h3>
              <div className="mock-pdf-table">
                <div className="mock-pdf-row head">
                  <span>Data</span>
                  <span>Histórico</span>
                  <span>Valor (R$)</span>
                  <span>Saldo (R$)</span>
                </div>
                {previewRows.map((row, index) => (
                  <div
                    key={`${page}-${index}`}
                    className={`mock-pdf-row ${index === 1 || index === 7 ? "highlight" : ""}`}
                  >
                    <span>{row[0]}</span>
                    <span>{row[1]}</span>
                    <span>{row[2]}</span>
                    <span>{row[3]}</span>
                  </div>
                ))}
              </div>
              <div className="mock-pdf-total">
                <span>Saldo final do período</span>
                <strong>R$ 38.548,73</strong>
              </div>
              <footer>
                <span>Documento demonstrativo · OFX Simples PRO</span>
                <span>
                  Página {page} de {conversion.pdf.pageCount}
                </span>
              </footer>
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}
