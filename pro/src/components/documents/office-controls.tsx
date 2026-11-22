"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { DocumentItem } from "@/data/documents-service";
import {
  officeService,
  saveOfficeFile,
  type PackagePreview,
  type VerificationStatus,
} from "@/data/office-service";
import { AuthorizedIdentityPdf } from "../identity-document-preview";

export function useOfficeEnabled() {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    void officeService
      .options(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setEnabled(value.enabled);
      })
      .catch(() => {});
    return () => abort.abort();
  }, []);
  return enabled;
}

const statuses: Record<VerificationStatus, string> = {
  pending: "A conferir",
  verified: "Verificado manualmente",
  attention: "Precisa de atenção",
  unprocessable: "Não processável",
};

export function OfficeDocumentControls({
  item,
  onSaved,
}: {
  item: DocumentItem;
  onSaved(): void;
}) {
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [kind, setKind] = useState(item.verification?.document_type ?? "");
  if (!item.source || !item.verification) return null;
  const save = async (status: VerificationStatus) => {
    setBusy(true);
    setError("");
    try {
      await officeService.verify(item.job_id, {
        source: item.source!,
        expected_version: item.verification!.version,
        operation_id: crypto.randomUUID(),
        status,
        ...(kind ? { document_type: kind } : {}),
      });
      onSaved();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível salvar a conferência.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label={`Conferência de ${item.filename}`}>
      <p>Disponível até {new Date(item.expires_at).toLocaleString("pt-BR")}.</p>
      {item.manual_association && <p>Cliente associado manualmente.</p>}
      {!item.movement_start_date && <p>Período a confirmar</p>}
      {!item.is_bank && item.movement_start_date && (
        <p>Data informada no documento: {item.movement_start_date}.</p>
      )}
      <p>
        {item.is_bank
          ? item.bank_reviewed
            ? "Revisão bancária concluída"
            : "Revisão bancária pendente"
          : statuses[item.verification.status]}
      </p>
      {item.verification.updated_at && (
        <p>
          Última conferência por você em{" "}
          {new Date(item.verification.updated_at).toLocaleString("pt-BR")}.
        </p>
      )}
      {/\.pdf$/i.test(item.filename) && (
        <button className="secondary-button" onClick={() => setOpen(!open)}>
          {open ? "Fechar PDF" : "Ver PDF original"}
        </button>
      )}
      {open && (
        <AuthorizedIdentityPdf
          key={attempt}
          batchId={item.batch_id}
          jobId={item.job_id}
          initialPage={1}
          hint="Confira o documento original antes de marcar como verificado."
          reload={() => setAttempt((a) => a + 1)}
        />
      )}
      {item.parts?.length ? (
        <div>
          <p>
            Páginas do original. Grupos de páginas não representam uma contagem
            de notas.
          </p>
          {item.parts.map((part) => (
            <div key={part.id}>
              <span>
                Páginas {part.pages.join(", ")}
                {part.requires_review
                  ? " — separação a confirmar no original"
                  : ""}
              </span>{" "}
              {!part.requires_review && (
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setError("");
                    try {
                      saveOfficeFile(
                        await officeService.part(item.job_id, part.id),
                        "paginas.pdf",
                      );
                    } catch (caught) {
                      setError(
                        caught instanceof Error
                          ? caught.message
                          : "Não foi possível baixar as páginas.",
                      );
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Baixar estas páginas
                </button>
              )}
            </div>
          ))}
        </div>
      ) : null}
      {!item.is_bank && (
        <>
          <label>
            Tipo para organização{" "}
            <select
              value={kind}
              disabled={busy}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="">Manter tipo identificado</option>
              <option value="bank_statement">Extrato</option>
              <option value="bank_receipt">Comprovante</option>
              <option value="fiscal_invoice">Nota fiscal</option>
              <option value="support_document">Documento de apoio</option>
              <option value="unknown">Desconhecido</option>
            </select>
          </label>
          <p>
            A conferência manual não confirma pagamento nem conciliação. Ela não
            cria movimentos bancários.
          </p>
          <div>
            {(Object.entries(statuses) as [VerificationStatus, string][]).map(
              ([status, label]) => (
                <button
                  key={status}
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void save(status)}
                >
                  {status === "verified"
                    ? "Marcar como verificado"
                    : `Marcar: ${label.toLowerCase()}`}
                </button>
              ),
            )}
          </div>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

export function OfficePackage({
  clientId,
  month,
  jobs,
}: {
  clientId: string;
  month: string;
  jobs: string[];
}) {
  const [preview, setPreview] = useState<PackagePreview | null>(null);
  const [partial, setPartial] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selection = { client_id: clientId, month, job_ids: jobs };
  const action = async (download = false) => {
    setBusy(true);
    setError("");
    try {
      if (download && preview)
        saveOfficeFile(
          await officeService.download(selection, preview.snapshot, partial),
          "pacote-contabilidade.zip",
        );
      else {
        setPreview(await officeService.preview(selection));
        setPartial(false);
      }
    } catch (caught) {
      setPreview(null);
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível gerar o pacote.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-label="Pacote para contabilidade">
      <h2>Pacote para contabilidade</h2>
      <p>
        Selecione até 30 documentos. O pacote inclui arquivos completos, seus
        movimentos revisados e um manifesto com as ressalvas. O mês filtra
        documentos; não comprova cobertura mensal.
      </p>
      <button
        className="primary-button"
        disabled={busy || !jobs.length || jobs.length > 30}
        onClick={() => void action()}
      >
        Conferir pacote ({jobs.length})
      </button>
      {preview && (
        <>
          <ul>
            {preview.items.map((item) => (
              <li key={item.job_id}>
                {item.filename ?? "Documento indisponível"}:{" "}
                {item.included ? "Incluído" : item.reason}
                {item.cautions?.map((caution) => (
                  <p key={caution}>{caution}</p>
                ))}
                {item.review_url && (
                  <Link href={item.review_url}>
                    {" — Abrir documento para conferir"}
                  </Link>
                )}
              </li>
            ))}
          </ul>
          {preview.items.some(
            (item) => !item.included || item.cautions?.length,
          ) && (
            <label>
              <input
                type="checkbox"
                checked={partial}
                onChange={(e) => setPartial(e.target.checked)}
              />
              Entendo as exclusões e ressalvas e quero baixar o pacote com essas
              limitações.
            </label>
          )}
          <button
            className="primary-button"
            disabled={
              busy ||
              !preview.items.some((i) => i.included) ||
              (preview.items.some((i) => !i.included || i.cautions?.length) &&
                !partial)
            }
            onClick={() => void action(true)}
          >
            Baixar pacote ZIP
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
