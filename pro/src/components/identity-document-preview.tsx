"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import {
  loadReceivedDocument,
  type PendingStatement,
} from "@/data/received-statements-service";
import styles from "./identity-document-preview.module.css";

const SimplePdfViewer = dynamic(() => import("./simple-pdf-viewer"), {
  ssr: false,
  loading: () => <p role="status">Preparando visualizador…</p>,
});

export function IdentityDocumentPreview({
  document,
}: {
  document: PendingStatement;
}) {
  const [attempt, setAttempt] = useState(0);
  const initialPage =
    document.source.references?.find(
      (ref) => Number.isInteger(ref.page) && ref.page > 0,
    )?.page ?? 1;
  return (
    <section className={styles.preview} aria-label="Documento original">
      <strong>{document.filename}</strong>
      {/\.pdf$/i.test(document.filename) ? (
        <AuthorizedIdentityPdf
          key={`${document.batch_id}:${document.job_id}:${attempt}`}
          batchId={document.batch_id}
          jobId={document.job_id}
          initialPage={initialPage}
          reload={() => setAttempt((value) => value + 1)}
        />
      ) : (
        <p>
          A prévia está disponível apenas para arquivos PDF. Confira os dados no
          arquivo original.
        </p>
      )}
    </section>
  );
}

function AuthorizedIdentityPdf({
  batchId,
  jobId,
  initialPage,
  reload,
}: {
  batchId: string;
  jobId: string;
  initialPage: number;
  reload(): void;
}) {
  const [state, setState] = useState<{ blob?: Blob; error?: string }>({});
  useEffect(() => {
    const controller = new AbortController();
    void loadReceivedDocument(batchId, jobId, controller.signal)
      .then((blob) => {
        if (!controller.signal.aborted) setState({ blob });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setState({
            error:
              error instanceof Error
                ? error.message
                : "Não foi possível carregar o PDF. Tente novamente.",
          });
      });
    return () => controller.abort();
  }, [batchId, jobId]);
  if (state.error)
    return (
      <div role="alert" className={styles.message}>
        <p>{state.error}</p>
        <button type="button" className="secondary-button" onClick={reload}>
          Recarregar PDF
        </button>
      </div>
    );
  return state.blob ? (
    <SimplePdfViewer
      blob={state.blob}
      initialPage={initialPage}
      reload={reload}
    />
  ) : (
    <p role="status">Carregando PDF…</p>
  );
}
