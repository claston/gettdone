"use client";
import type {
  PdfNavigationRequest,
  PdfReadingPosition,
} from "./source-location";
import dynamic from "next/dynamic";
import { useState } from "react";
import type { Conversion, WorkspaceRepository } from "@/domain/workspace";
import { useAuthorizedDocument } from "./use-authorized-document";
import type { PdfReviewControls } from "./pdf-interactions";

const PdfCanvasViewer = dynamic(() => import("./pdf-canvas-viewer"), {
  ssr: false,
  loading: () => <p role="status">Preparando visualizador…</p>,
});

export function RealDocumentViewer(props: {
  conversion: Conversion;
  repository: WorkspaceRepository;
  page: number;
  setPage(page: number): void;
  navigation: PdfNavigationRequest | null;
  onAvailability(available: boolean): void;
  onReadingPosition?(position: PdfReadingPosition): void;
  review?: PdfReviewControls;
}) {
  const [attempt, setAttempt] = useState(0);
  return (
    <AuthorizedPdf
      key={`${props.conversion.id}:${props.conversion.pdf?.url}:${attempt}`}
      {...props}
      attempt={attempt}
      retry={() => setAttempt((value) => value + 1)}
    />
  );
}
function AuthorizedPdf({
  conversion,
  repository,
  attempt,
  retry,
  ...props
}: Parameters<typeof RealDocumentViewer>[0] & {
  attempt: number;
  retry(): void;
}) {
  const state = useAuthorizedDocument(conversion.id, repository, attempt);
  return (
    <section
      className="pdf-panel controlled-pdf"
      aria-label="Documento original"
    >
      {state.url ? (
        <PdfCanvasViewer
          url={state.url}
          name={conversion.fileName}
          page={props.page}
          onPage={props.setPage}
          navigation={props.navigation}
          retry={retry}
          onAvailability={props.onAvailability}
          onReadingPosition={props.onReadingPosition}
          review={props.review}
        />
      ) : (
        <div className="viewer-state" role={state.error ? "alert" : "status"}>
          <p>{state.error ?? "Carregando PDF…"}</p>
          {state.error && (
            <button className="secondary-button" onClick={retry}>
              Recarregar PDF
            </button>
          )}
        </div>
      )}
    </section>
  );
}
