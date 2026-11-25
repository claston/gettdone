"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { FileText, Link2, AlertTriangle, ChevronLeft } from "lucide-react";
import {
  loadFinancialMonth,
  decimalCents,
  financialMoney,
  type FinancialMonth,
  type CanonicalMovement,
} from "@/data/financial-center-service";
import {
  conferenceService,
  type DocumentaryEvidence,
  type DocumentaryLinks,
} from "@/data/document-conference-service";
import { documentsService, type DocumentItem } from "@/data/documents-service";
import { dateLabel, monthLabel } from "@/domain/financial-center";
import { AuthorizedIdentityPdf } from "../identity-document-preview";
import styles from "./conference.module.css";

const errorText = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Não foi possível concluir. Tente novamente.";
const amount = (row: CanonicalMovement) =>
  financialMoney(decimalCents(row.amount), row.currency);
const available = (expiry?: string | null) =>
  !expiry || Date.parse(expiry) > Date.now();

export function DocumentConference({
  clientId,
  month,
  accountId,
  returnTo,
  initialDocument,
}: {
  clientId: string;
  month: string;
  accountId: string;
  returnTo: string;
  initialDocument?: DocumentItem | null;
}) {
  const [revision, setRevision] = useState(0);
  const [cursors, setCursors] = useState<string[]>([""]);
  const [selectedId, setSelectedId] = useState("");
  const [mobileDetail, setMobileDetail] = useState(false);
  const [snapshot, setSnapshot] = useState<{
    key: string;
    data?: FinancialMonth;
    error?: string;
  } | null>(null);
  const query = new URLSearchParams({ limit: "30" });
  if (month && month !== "unknown") query.set("month", month);
  if (accountId) query.set("account", accountId);
  if (cursors.at(-1)) query.set("cursor", cursors.at(-1)!);
  const request = query.toString();
  const key = `${clientId}:${request}:${revision}`;
  const current = snapshot?.key === key ? snapshot : null;
  const data =
    current?.data && available(current.data.access_expires_at)
      ? current.data
      : null;
  const selected =
    data?.movements.find((row) => row.id === selectedId) ??
    (!initialDocument ? data?.movements[0] : undefined);
  useEffect(() => {
    const controller = new AbortController();
    void loadFinancialMonth(clientId, request, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setSnapshot({ key, data });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setSnapshot({ key, error: errorText(error) });
      });
    return () => controller.abort();
  }, [clientId, request, key]);
  useEffect(() => {
    const expiry = current?.data?.access_expires_at;
    if (!expiry || !available(expiry)) return;
    const timer = window.setTimeout(
      () => setRevision((value) => value + 1),
      Math.max(0, Math.min(Date.parse(expiry) - Date.now(), 2147483647)),
    );
    return () => window.clearTimeout(timer);
  }, [current]);
  const refresh = () => setRevision((value) => value + 1);
  if (current?.error)
    return (
      <div className={styles.empty} role="alert">
        <p>{current.error}</p>
        <button className="secondary-button" onClick={refresh}>
          Tentar novamente
        </button>
      </div>
    );
  if (current?.data && !data)
    return (
      <div role="alert">
        <p>
          O prazo desta consulta terminou. Atualize para consultar os documentos
          ainda disponíveis.
        </p>
        <button className="secondary-button" onClick={refresh}>
          Atualizar conferência
        </button>
      </div>
    );
  if (!data) return <p role="status">Consultando movimentações disponíveis…</p>;
  const sameValue =
    selected &&
    data.movements.some(
      (row) =>
        row.id !== selected.id &&
        row.date === selected.date &&
        row.amount === selected.amount &&
        row.currency === selected.currency,
    );
  return (
    <section
      aria-label="Conferência por movimentação"
      className={styles.workspace}
    >
      <div className={styles.heading}>
        <div>
          <h2>Movimentações e seus documentos</h2>
          <p>
            {monthLabel(data.month)} · selecione uma movimentação para conferir.
          </p>
        </div>
      </div>
      {(data.legacy_sources > 0 || data.unavailable_sources > 0) && (
        <p className={styles.warning}>
          Alguns documentos deste cliente não estão disponíveis nesta consulta.
          A lista pode estar incompleta; confira os arquivos antes de usar os
          totais.
        </p>
      )}
      {initialDocument && (
        <p className={styles.notice}>
          Documento escolhido: <strong>{initialDocument.filename}</strong>.
          Selecione a movimentação que deseja comparar.
        </p>
      )}
      {!data.movements.length ? (
        <div className={styles.empty}>
          <h3>Nenhuma movimentação disponível neste recorte</h3>
          <p>
            Confira a conta e o mês ou consulte os arquivos recebidos. A
            ausência de movimentos nesta lista não confirma ausência de
            atividade.
          </p>
        </div>
      ) : (
        <div
          className={`${styles.layout} ${mobileDetail ? styles.detailOpen : ""}`}
        >
          <div className={styles.list} aria-label="Movimentações">
            {data.movements.map((row) => (
              <button
                className={styles.row}
                key={row.id}
                aria-pressed={selected?.id === row.id}
                onClick={() => {
                  setSelectedId(row.id);
                  setMobileDetail(true);
                }}
              >
                <span className={styles.rowTop}>
                  <span>{dateLabel(row.date)}</span>
                  <strong>{amount(row)}</strong>
                </span>
                <strong>{row.description}</strong>
                <span className={styles.meta}>
                  {data.accounts.find(
                    (account) => account.id === row.account_id,
                  )?.label ?? "Conta a confirmar"}
                </span>
                <span
                  className={
                    row.pending || row.possible_duplicate
                      ? styles.badgeWarning
                      : styles.badge
                  }
                >
                  {row.possible_duplicate
                    ? "Possível duplicidade · conferir"
                    : row.pending
                      ? "Revisão bancária pendente"
                      : "Lançamento revisado"}
                </span>
              </button>
            ))}
            <div className={styles.pagination}>
              {cursors.length > 1 && (
                <button
                  className="secondary-button"
                  onClick={() => {
                    setCursors((values) => values.slice(0, -1));
                    setSelectedId("");
                  }}
                >
                  Movimentos anteriores
                </button>
              )}
              {data.next_cursor && (
                <button
                  className="secondary-button"
                  onClick={() => {
                    setCursors((values) => [...values, data.next_cursor!]);
                    setSelectedId("");
                  }}
                >
                  Próximos movimentos
                </button>
              )}
            </div>
          </div>
          <div className={styles.detail}>
            <button
              className={`secondary-button ${styles.back}`}
              onClick={() => setMobileDetail(false)}
            >
              <ChevronLeft size={16} aria-hidden="true" />
              Voltar às movimentações
            </button>
            {selected ? (
              <MovementDocuments
                key={`${clientId}:${selected.id}:${selected.version}`}
                clientId={clientId}
                movement={selected}
                enabled={data.features?.document_conference === true}
                sameValue={Boolean(sameValue)}
                returnTo={returnTo}
                initialDocument={initialDocument}
                onExpired={refresh}
              />
            ) : (
              <p>Escolha uma movimentação para comparar os documentos.</p>
            )}
          </div>
        </div>
      )}
      <p className={styles.meta}>
        Esta consulta não comprova a cobertura completa do período nem a
        conciliação de todos os movimentos.
      </p>
    </section>
  );
}

function MovementDocuments({
  clientId,
  movement,
  enabled,
  sameValue,
  returnTo,
  initialDocument,
  onExpired,
}: {
  clientId: string;
  movement: CanonicalMovement;
  enabled: boolean;
  sameValue: boolean;
  returnTo: string;
  initialDocument?: DocumentItem | null;
  onExpired(): void;
}) {
  const [documents, setDocuments] = useState<
    Array<Pick<DocumentItem, "job_id" | "batch_id" | "filename" | "expires_at">>
  >(initialDocument ? [initialDocument] : []);
  const [next, setNext] = useState<string | null>(null);
  const [documentCursor, setDocumentCursor] = useState("");
  const [documentError, setDocumentError] = useState("");
  const [chosen, setChosen] = useState(initialDocument?.job_id ?? "");
  const [evidence, setEvidence] = useState<{
    key: string;
    data?: DocumentaryEvidence;
    error?: string;
  } | null>(null);
  const [links, setLinks] = useState<DocumentaryLinks | null>(null);
  const [linkError, setLinkError] = useState("");
  const [revision, setRevision] = useState(0);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [actionError, setActionError] = useState("");
  const [preview, setPreview] = useState<{
    job: string;
    batch: string;
    page: number;
  } | null>(null);
  const [pdfAttempt, setPdfAttempt] = useState(0);
  const originalPanel = useRef<HTMLElement | null>(null);
  const command = useRef<AbortController | null>(null);
  const operation = useRef<{ key: string; id: string } | null>(null);
  const current = evidence?.key === chosen ? evidence : null;
  const doc =
    current?.data && available(current.data.expires_at) ? current.data : null;
  useEffect(() => () => command.current?.abort(), []);
  useEffect(() => {
    if (preview) {
      originalPanel.current?.scrollIntoView?.({
        block: "start",
        behavior: "smooth",
      });
      originalPanel.current?.focus({ preventScroll: true });
    }
  }, [preview]);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const query = new URLSearchParams({ client_id: clientId, category: "all" });
    if (documentCursor) query.set("cursor", documentCursor);
    void documentsService
      .list(query.toString(), controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        const candidates = result.items.filter(
          (item) =>
            item.context.client_id === clientId &&
            !item.is_expired &&
            available(item.expires_at) &&
            item.status === "completed" &&
            item.is_bank !== true &&
            ["receipts", "invoices"].includes(item.category),
        );
        setDocuments((previous) => [
          ...new Map(
            [...previous, ...candidates].map((item) => [item.job_id, item]),
          ).values(),
        ]);
        setNext(result.next_cursor);
        setDocumentError("");
      })
      .catch((error) => {
        if (!controller.signal.aborted) setDocumentError(errorText(error));
      });
    return () => controller.abort();
  }, [clientId, documentCursor, enabled, revision]);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void conferenceService
      .links(clientId, movement.id, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          if (available(result.access_expires_at)) setLinks(result);
          else {
            setLinks(null);
            setLinkError(
              "O prazo desta consulta terminou. Consulte as relações novamente.",
            );
            return;
          }
          setLinkError("");
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setLinks(null);
          setLinkError(errorText(error));
        }
      });
    return () => controller.abort();
  }, [clientId, movement.id, revision, enabled]);
  useEffect(() => {
    if (!chosen || !enabled) return;
    const controller = new AbortController();
    void conferenceService
      .document(clientId, chosen, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setEvidence({ key: chosen, data });
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setEvidence({ key: chosen, error: errorText(error) });
      });
    return () => controller.abort();
  }, [clientId, chosen, revision, enabled]);
  useEffect(() => {
    const expiries = [doc?.expires_at, links?.access_expires_at].filter(
      (value): value is string => Boolean(value),
    );
    if (!expiries.length) return;
    const timer = window.setTimeout(
      () => {
        command.current?.abort();
        setEvidence(null);
        setLinks(null);
        setPreview(null);
        onExpired();
      },
      Math.max(
        0,
        Math.min(
          ...expiries.map((value) => Date.parse(value) - Date.now()),
          2147483647,
        ),
      ),
    );
    return () => window.clearTimeout(timer);
  }, [doc?.expires_at, links?.access_expires_at, onExpired]);
  async function act(
    kind: "confirm" | "undo",
    link?: DocumentaryLinks["links"][number],
  ) {
    if (
      command.current ||
      (kind === "confirm" &&
        (!doc || !acknowledged || !available(doc.expires_at)))
    )
      return;
    const controller = new AbortController();
    command.current = controller;
    setBusy(true);
    setActionError("");
    setFeedback("");
    const operationKey = `${kind}:${movement.id}:${movement.version}:${doc?.source ?? ""}:${link?.id ?? ""}:${link?.version ?? ""}`;
    if (operation.current?.key !== operationKey)
      operation.current = { key: operationKey, id: crypto.randomUUID() };
    try {
      if (kind === "confirm" && doc)
        await conferenceService.confirm(
          clientId,
          movement.id,
          {
            job_id: doc.job_id,
            source: doc.source,
            movement_version: movement.version,
            operation_id: operation.current.id,
          },
          controller.signal,
        );
      else if (link)
        await conferenceService.undo(
          clientId,
          movement.id,
          link.id,
          {
            expected_version: link.version,
            operation_id: operation.current.id,
          },
          controller.signal,
        );
      if (!controller.signal.aborted) {
        operation.current = null;
        setAcknowledged(false);
        setRevision((value) => value + 1);
        setFeedback(
          kind === "confirm"
            ? "Relação documental confirmada."
            : "Relação desfeita.",
        );
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setActionError(errorText(error));
        setAcknowledged(false);
      }
    } finally {
      if (command.current === controller) {
        command.current = null;
        setBusy(false);
      }
    }
  }
  function choose(job: string) {
    setChosen(job);
    setAcknowledged(false);
    setActionError("");
    setFeedback("");
    setPreview(null);
  }
  const alreadyLinked =
    doc &&
    links?.links.some(
      (link) =>
        link.document.job_id === doc.job_id && link.state === "confirmed",
    );
  return (
    <section aria-label="Comparação documental">
      <div className={styles.heading}>
        <div>
          <h3>Conferir a relação</h3>
          <p>{movement.description}</p>
        </div>
        <strong className={styles.money}>{amount(movement)}</strong>
      </div>
      <p className={styles.meta}>
        {dateLabel(movement.date)} · {movement.source.filename}
        {movement.source.page ? ` · página ${movement.source.page}` : ""}
      </p>
      <div className={styles.actions}>
        {/\.pdf$/i.test(movement.source.filename) && (
          <button
            className="secondary-button"
            onClick={() =>
              setPreview({
                job: movement.source.job_id,
                batch: movement.source.batch_id,
                page: movement.source.page ?? 1,
              })
            }
          >
            <FileText size={16} aria-hidden="true" />
            Ver extrato original
          </button>
        )}
        <Link
          className={styles.textLink}
          href={`/review/${encodeURIComponent(movement.source.batch_id)}/${encodeURIComponent(movement.source.job_id)}?return_to=${encodeURIComponent(returnTo)}`}
        >
          Revisar lançamento
        </Link>
      </div>
      {sameValue && (
        <p className={styles.warning}>
          <AlertTriangle size={18} aria-hidden="true" />
          Há outra movimentação de mesmo valor e data nesta página. Compare o
          destinatário antes de relacionar os documentos.
        </p>
      )}
      {movement.possible_duplicate && (
        <p className={styles.warning}>
          Este lançamento tem indicação de possível duplicidade. Confira a
          origem; nenhum movimento foi removido.
        </p>
      )}
      {enabled ? (
        <>
          <div className={styles.relations}>
            <h4>Documentos relacionados</h4>
            {linkError && (
              <div role="alert">
                <p>{linkError}</p>
                <button
                  className="secondary-button"
                  onClick={() => setRevision((value) => value + 1)}
                >
                  Consultar relações novamente
                </button>
              </div>
            )}
            {!links && !linkError && <p role="status">Consultando relações…</p>}
            {links?.links.map((link) => (
              <div className={styles.relation} key={link.id}>
                <button
                  className={styles.textButton}
                  disabled={busy}
                  onClick={() => {
                    setDocuments((previous) =>
                      previous.some(
                        (item) => item.job_id === link.document.job_id,
                      )
                        ? previous
                        : [...previous, link.document],
                    );
                    choose(link.document.job_id);
                  }}
                >
                  <Link2 size={16} aria-hidden="true" />
                  {link.document.filename}
                </button>
                <span
                  className={
                    link.state === "confirmed"
                      ? styles.badge
                      : styles.badgeWarning
                  }
                >
                  {link.state === "confirmed"
                    ? "Relação documental confirmada"
                    : "Origem alterada · conferir novamente"}
                </span>
                <button
                  className={styles.textButton}
                  disabled={busy}
                  onClick={() => void act("undo", link)}
                >
                  Desfazer relação
                </button>
              </div>
            ))}
            {links && !links.links.length && (
              <p>Nenhum documento relacionado a esta movimentação.</p>
            )}
            {Boolean(links?.unavailable_links) && (
              <p className={styles.warning}>
                Um ou mais documentos relacionados não estão disponíveis. A
                conferência pode estar incompleta.
              </p>
            )}
            {links?.partial && (
              <p className={styles.warning}>
                A lista de relações está parcial. Não considere esta conferência
                completa.
              </p>
            )}
          </div>
          <label className={styles.field}>
            Documento para comparar
            <select
              value={chosen}
              disabled={busy}
              onChange={(event) => choose(event.target.value)}
            >
              <option value="">Selecione uma nota ou um comprovante</option>
              {documents
                .filter((item) => available(item.expires_at))
                .map((item) => (
                  <option key={item.job_id} value={item.job_id}>
                    {item.filename}
                  </option>
                ))}
            </select>
          </label>
          <p className={styles.meta}>
            Notas e comprovantes disponíveis deste cliente, inclusive de outros
            períodos. A seleção não indica uma correspondência automática.
          </p>
          {documentError && <p role="alert">{documentError}</p>}
          {next && (
            <button
              className={styles.textButton}
              onClick={() => setDocumentCursor(next)}
            >
              Carregar mais documentos do cliente
            </button>
          )}
          {current?.error && (
            <div role="alert">
              <p>{current.error}</p>
              <button
                className="secondary-button"
                onClick={() => setRevision((value) => value + 1)}
              >
                Consultar documento novamente
              </button>
            </div>
          )}
          {chosen && !current && <p role="status">Consultando documento…</p>}
          {doc && (
            <div className={styles.evidence}>
              <h4>{doc.filename}</h4>
              <p>{doc.notice}</p>
              {doc.observations.filter(
                (field) =>
                  typeof field.attributes.label === "string" &&
                  typeof field.attributes.value === "string",
              ).length > 0 && (
                <dl>
                  {doc.observations
                    .filter(
                      (field) =>
                        typeof field.attributes.label === "string" &&
                        typeof field.attributes.value === "string",
                    )
                    .map((field, index) => (
                      <div key={index}>
                        <dt>{String(field.attributes.label)}</dt>
                        <dd>{String(field.attributes.value)}</dd>
                      </div>
                    ))}
                </dl>
              )}
              {/\.pdf$/i.test(doc.filename) ? (
                <button
                  className="secondary-button"
                  onClick={() =>
                    setPreview({
                      job: doc.job_id,
                      batch: doc.batch_id,
                      page: doc.observations[0]?.source_refs[0]?.page ?? 1,
                    })
                  }
                >
                  Ver documento original
                </button>
              ) : (
                <Link
                  className={styles.textLink}
                  href={`/review/${encodeURIComponent(doc.batch_id)}/${encodeURIComponent(doc.job_id)}?return_to=${encodeURIComponent(returnTo)}`}
                >
                  Abrir revisão do documento
                </Link>
              )}
              <p className={styles.meta}>
                Disponível até{" "}
                {new Date(doc.expires_at).toLocaleString("pt-BR")}.
              </p>
              {!alreadyLinked && (
                <>
                  <label className={styles.check}>
                    <input
                      type="checkbox"
                      checked={acknowledged}
                      disabled={busy || !links || Boolean(linkError)}
                      onChange={(event) =>
                        setAcknowledged(event.target.checked)
                      }
                    />
                    Conferi os documentos e reconheço a relação com esta
                    movimentação.
                  </label>
                  <div className={styles.footer}>
                    <button
                      className="secondary-button"
                      disabled={busy}
                      onClick={() => {
                        choose("");
                        setFeedback("Documento deixado sem nova confirmação.");
                      }}
                    >
                      Revisar depois
                    </button>
                    <button
                      className="primary-button"
                      disabled={
                        busy || !acknowledged || !links || Boolean(linkError)
                      }
                      onClick={() => void act("confirm")}
                    >
                      {busy ? "Salvando…" : "Confirmar relação documental"}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
          <p className={styles.meta}>
            O vínculo identifica documentos de apoio; não confirma quitação
            fiscal, não distribui valores de pagamentos parciais e não conclui a
            conciliação da conta.
          </p>
        </>
      ) : (
        <p className={styles.notice}>
          A comparação com vínculos documentais ainda não está disponível neste
          ambiente. A revisão do lançamento e os arquivos originais continuam
          acessíveis.
        </p>
      )}
      {actionError && (
        <p role="alert" className={styles.warning}>
          {actionError}
        </p>
      )}
      {feedback && <p role="status">{feedback}</p>}
      {preview && (
        <section
          ref={originalPanel}
          tabIndex={-1}
          className={styles.original}
          aria-label="Original para comparação"
        >
          <div className={styles.heading}>
            <h4>Documento original</h4>
            <button
              className="secondary-button"
              onClick={() => setPreview(null)}
            >
              Fechar original
            </button>
          </div>
          <AuthorizedIdentityPdf
            key={`${preview.job}:${preview.page}:${pdfAttempt}`}
            jobId={preview.job}
            batchId={preview.batch}
            initialPage={preview.page}
            hint="Confira data, valor e identificação das partes no documento original."
            reload={() => setPdfAttempt((value) => value + 1)}
          />
        </section>
      )}
    </section>
  );
}
