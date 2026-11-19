"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  downloadFiscal,
  fiscalCommand,
  loadFiscalWorkspace,
  uploadFiscalNote,
  type FiscalMovement,
  type FiscalNote,
  type FiscalWorkspaceData,
} from "@/data/fiscal-service";
import { decimalCents, financialMoney } from "@/data/financial-center-service";
import { dateLabel } from "@/domain/financial-center";
import styles from "./fiscal-workspace.module.css";
import { ClassificationShadow } from "./classification-shadow";

const money = (value: string | null, currency = "BRL") =>
  value === null ? "A conferir" : financialMoney(decimalCents(value), currency);
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "Operação indisponível.";
const evidence: Record<string, string> = {
  amount: "Valor compatível",
  date_window: "Datas próximas",
  counterparty_tax_id: "CPF/CNPJ da contraparte",
  invoice_reference: "Referência à nota",
};
type Props = {
  clientId: string;
  month: string;
  account: string;
  mode: "invoices" | "taxes" | "revenue";
  initialMovementId?: string;
  onSaved?: () => void;
};

export function FiscalWorkspace({
  clientId,
  month,
  account,
  mode,
  initialMovementId,
  onSaved,
}: Props) {
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [revision, setRevision] = useState(0);
  const [snapshot, setSnapshot] = useState<{
    key: string;
    data: FiscalWorkspaceData;
  } | null>(null);
  const [failure, setFailure] = useState<{
    key: string;
    message: string;
  } | null>(null);
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [chosenMovement, setChosenMovement] = useState<FiscalMovement | null>(
    null,
  );
  const [allocation, setAllocation] = useState("");
  const pendingOperation = useRef<{ key: string; id: string } | null>(null);
  const query = new URLSearchParams({ month, offset: String(offset) });
  if (account) query.set("account", account);
  if (selectedId) query.set("document_id", selectedId);
  if (initialMovementId) query.set("movement_id", initialMovementId);
  const request = query.toString();
  const key = `${clientId}:${request}:${revision}`;
  const data = snapshot?.key === key ? snapshot.data : null;
  const error = failure?.key === key ? failure.message : null;
  const note = data?.documents.find((item) => item.id === selectedId);
  const refresh = () => {
    setSnapshot(null);
    setChosenMovement(null);
    setRevision((value) => value + 1);
  };

  useEffect(() => {
    const controller = new AbortController();
    void loadFiscalWorkspace(clientId, request, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setSnapshot({ key, data: result });
          setFailure(null);
        }
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setSnapshot(null);
          setFailure({ key, message: errorText(caught) });
        }
      });
    return () => controller.abort();
  }, [clientId, request, key]);

  useEffect(() => {
    const update = () => {
      setSnapshot(null);
      setRevision((value) => value + 1);
    };
    const focus = () => {
      if (document.visibilityState === "visible") update();
    };
    const pending = data?.imports.some((item) =>
      ["queued", "processing"].includes(item.state),
    );
    const untilExpiry = data?.access_expires_at
      ? Math.max(0, Date.parse(data.access_expires_at) - Date.now())
      : Infinity;
    const delay = Math.min(untilExpiry, pending ? 4000 : Infinity, 2147483647);
    const timer = Number.isFinite(delay)
      ? window.setTimeout(update, delay)
      : undefined;
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
    };
  }, [data]);

  async function action(run: () => Promise<unknown>, reload = true) {
    if (busy) return;
    setBusy(true);
    setActionError("");
    try {
      await run();
      if (reload) {
        refresh();
        onSaved?.();
      }
    } catch (caught) {
      setActionError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }
  async function command(path: string, body: object) {
    const commandKey = JSON.stringify([clientId, path, body]);
    if (pendingOperation.current?.key !== commandKey)
      pendingOperation.current = { key: commandKey, id: crypto.randomUUID() };
    const result = await fiscalCommand(clientId, path, {
      ...body,
      operation_id: pendingOperation.current.id,
    });
    pendingOperation.current = null;
    return result;
  }
  const visibleMovements =
    data?.movements.filter(
      (row) => !initialMovementId || row.id === initialMovementId,
    ) ?? [];
  const candidate =
    chosenMovement ??
    visibleMovements.find((row) => row.id === initialMovementId) ??
    null;
  return (
    <section
      className={styles.panel}
      aria-label={
        mode === "invoices"
          ? "Notas e conciliação"
          : mode === "taxes"
            ? "Gestão Tributária"
            : "Faturamento estimado"
      }
    >
      <div className={styles.heading}>
        <div>
          <h2>
            {mode === "invoices"
              ? "Notas e conciliação"
              : mode === "taxes"
                ? "Gestão Tributária"
                : "Faturamento estimado"}
          </h2>
          <p>
            {mode === "invoices"
              ? "Relacione notas aos pagamentos e recebimentos da empresa."
              : mode === "taxes"
                ? "Classifique pagamentos tributários e confira o que ainda precisa de identificação."
                : "Confira separadamente as vendas documentadas e os recebimentos identificados."}
          </p>
        </div>
        <button className="secondary-button" onClick={refresh} disabled={busy}>
          Atualizar
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {actionError && <p role="alert">{actionError}</p>}
      {!data && !error && (
        <p role="status">Consultando documentos e movimentos…</p>
      )}
      {data && (
        <>
          {data.truncated && (
            <p role="alert">
              Há mais de 10.000 movimentos. Filtre uma conta para conferir os
              totais.
            </p>
          )}
          <p className={styles.notice}>
            A cobertura depende dos documentos acessíveis. Notas seguem a data
            de emissão; movimentos seguem a data bancária.
            {account &&
              " O filtro de conta aplica-se aos movimentos; as notas pertencem à empresa."}
          </p>
          {data.classification_shadow_enabled && (
            <ClassificationShadow
              key={clientId}
              clientId={clientId}
              sources={[
                ...data.movements.map((row) => ({
                  id: row.id,
                  version: row.version,
                  kind: "movement" as const,
                  label: row.description,
                })),
                ...data.documents
                  .filter(
                    (row) => row.kind === "nfe" && row.source.format === "xml",
                  )
                  .map((row) => ({
                    id: row.id,
                    version: row.version,
                    kind: "invoice" as const,
                    label: `NF-e ${row.number ?? "sem número"}`,
                  })),
              ]}
            />
          )}
          {mode === "revenue" && (
            <div className={styles.metrics}>
              <Metric
                label="Vendas documentadas"
                value={money(data.summary.documented_sales)}
              />
              <Metric
                label="Recebimentos operacionais identificados"
                value={money(data.summary.operating_receipts)}
              />
              <Metric
                label="Entradas sem classificação"
                value={money(data.summary.unclassified_inflows)}
              />
              <Metric
                label="Outras entradas classificadas"
                value={money(data.summary.excluded_inflows)}
              />
            </div>
          )}
          {mode === "revenue" && (
            <p>
              Os indicadores representam bases distintas e não são somados.{" "}
              {data.summary.pending_documents} nota(s) ainda precisam de
              revisão.
            </p>
          )}
          {mode === "taxes" && (
            <>
              <div className={styles.metrics}>
                {Object.entries(data.summary.tax_payments).map(
                  ([category, value]) => (
                    <Metric
                      key={category}
                      label={data.categories[category]}
                      value={money(value)}
                    />
                  ),
                )}
              </div>
              {!Object.keys(data.summary.tax_payments).length && (
                <p>Nenhum pagamento tributário confirmado neste recorte.</p>
              )}
              <p>
                O pagamento de uma guia pode reunir tributos. A competência e o
                código da receita ficam pendentes quando não informados.
              </p>
            </>
          )}
          {mode === "invoices" && data.features.invoices && (
            <>
              <label className={styles.upload}>
                Importar nota XML ou PDF
                <input
                  type="file"
                  accept=".xml,.pdf"
                  disabled={busy}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file)
                      void action(() => uploadFiscalNote(clientId, file));
                  }}
                />
              </label>
              <p>
                Uma nota por arquivo, até 5 MB e 10 páginas. PDFs exigem
                conferência. Situação extraída do arquivo, sem consulta online
                ao Fisco. As notas e seus arquivos ficam acessíveis por até 24
                horas, conforme a retenção do PRO.
              </p>
              {data.imports
                .filter((item) => item.state !== "ready")
                .map((item) => (
                  <p key={item.id} role="status">
                    {item.filename}:{" "}
                    {item.error ??
                      {
                        uploading: "envio pendente",
                        queued: "aguardando processamento",
                        processing: "processando",
                        failed: "falha",
                      }[item.state] ??
                      item.state}
                  </p>
                ))}
              <label>
                Nota fiscal
                <select
                  aria-label="Nota fiscal"
                  value={selectedId}
                  onChange={(event) => {
                    setSelectedId(event.target.value);
                    setChosenMovement(null);
                    setAllocation("");
                  }}
                >
                  <option value="">Selecione uma nota</option>
                  {data.documents.map((item) => (
                    <option value={item.id} key={item.id}>
                      {item.kind.toUpperCase()}{" "}
                      {item.number ?? "número a conferir"} ·{" "}
                      {item.issued_on
                        ? dateLabel(item.issued_on)
                        : "data a conferir"}{" "}
                      · {money(item.gross_amount)}
                      {item.review_required ? " · revisar" : ""}
                    </option>
                  ))}
                </select>
              </label>
              {!data.documents.length && (
                <p>
                  Nenhuma nota acessível. Importe um documento para começar.
                </p>
              )}
              {note && (
                <>
                  <div className={styles.note}>
                    <p>
                      <strong>
                        {note.direction === "issued"
                          ? "Nota emitida pela empresa"
                          : note.direction === "received"
                            ? "Nota recebida pela empresa"
                            : "Empresa a confirmar"}
                      </strong>
                    </p>
                    <p>
                      Emitente:{" "}
                      {note.issuer.name ??
                        note.issuer.tax_id ??
                        "Não identificado"}{" "}
                      · Destinatário:{" "}
                      {note.recipient.name ??
                        note.recipient.tax_id ??
                        "Não identificado"}
                    </p>
                    <p>
                      Bruto: {money(note.gross_amount)} · Líquido informado:{" "}
                      {money(note.net_amount)}
                    </p>
                    {Object.entries(note.taxes).map(([name, value]) => (
                      <span key={name} className={styles.chip}>
                        {name}: {money(value)}
                      </span>
                    ))}
                    <p>
                      Situação:{" "}
                      {{
                        reported: "informada no documento",
                        cancelled: "cancelada",
                        substituted: "substituída",
                        duplicate: "duplicada",
                      }[note.status] ?? "a conferir"}
                    </p>
                    <button
                      className="secondary-button"
                      disabled={busy}
                      onClick={() =>
                        void action(
                          () =>
                            downloadFiscal(
                              clientId,
                              `documents/${note.id}/source`,
                              `nota.${note.source.format === "xml" ? "xml" : "pdf"}`,
                            ),
                          false,
                        )
                      }
                    >
                      Baixar nota original
                    </button>
                  </div>
                  <NoteReview
                    key={`${note.id}:${note.version}`}
                    note={note}
                    busy={busy}
                    save={(changes) =>
                      action(() =>
                        command(`/fiscal/documents/${note.id}/review`, {
                          expected_version: note.version,
                          changes,
                        }),
                      )
                    }
                  />
                  <h3>Correspondências sugeridas</h3>
                  {!data.suggestions.length && (
                    <p>
                      Não há sugestões neste mês e conta. É possível selecionar
                      um movimento manualmente.
                    </p>
                  )}
                  {data.suggestions.map((suggestion) => (
                    <div
                      className={styles.suggestion}
                      key={suggestion.movement_id}
                    >
                      <div>
                        <strong>{suggestion.movement.description}</strong>
                        <p>
                          {dateLabel(suggestion.movement.date)} ·{" "}
                          {money(suggestion.movement.amount)}
                        </p>
                        <small>
                          {suggestion.evidence
                            .map((value) => evidence[value] ?? value)
                            .join(" · ")}
                          {suggestion.confidence === "weak"
                            ? " · Evidência limitada"
                            : ""}
                        </small>
                      </div>
                      <button
                        className="secondary-button"
                        disabled={busy}
                        onClick={() => {
                          setChosenMovement(suggestion.movement);
                          setAllocation(suggestion.amount);
                        }}
                      >
                        Conferir vínculo
                      </button>
                    </div>
                  ))}
                  <label>
                    Movimento para vincular
                    <select
                      aria-label="Movimento para vincular"
                      value={candidate?.id ?? ""}
                      onChange={(event) => {
                        const row =
                          data.movements.find(
                            (item) => item.id === event.target.value,
                          ) ?? null;
                        setChosenMovement(row);
                        setAllocation(row?.amount.replace("-", "") ?? "");
                      }}
                    >
                      <option value="">
                        Selecione um movimento desta página
                      </option>
                      {candidate &&
                        !data.movements.some(
                          (row) => row.id === candidate.id,
                        ) && (
                          <option value={candidate.id}>
                            {candidate.description}
                          </option>
                        )}
                      {data.movements.map((row) => (
                        <option value={row.id} key={row.id}>
                          {dateLabel(row.date)} · {row.description} ·{" "}
                          {money(row.amount, row.currency)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className={styles.actions}>
                    <label>
                      Valor a vincular (R$)
                      <input
                        inputMode="decimal"
                        value={allocation}
                        onChange={(event) => setAllocation(event.target.value)}
                      />
                    </label>
                    <button
                      className="primary-button"
                      disabled={
                        busy ||
                        !candidate ||
                        note.review_required ||
                        note.direction === "unconfirmed"
                      }
                      onClick={() =>
                        candidate &&
                        void action(() =>
                          command("/fiscal/links", {
                            document_id: note.id,
                            movement_id: candidate.id,
                            amount: allocation.replace(",", "."),
                            document_version: note.version,
                            movement_version: candidate.version,
                          }),
                        )
                      }
                    >
                      Confirmar vínculo
                    </button>
                  </div>
                  <p>
                    É possível vincular parte do valor. Diferenças continuam
                    pendentes; valores do extrato não são ajustados.
                  </p>
                </>
              )}
              <h3>Vínculos registrados</h3>
              {data.links
                .filter(
                  (link) =>
                    (!selectedId || link.document_id === selectedId) &&
                    (!initialMovementId ||
                      link.movement_id === initialMovementId),
                )
                .map((link) => (
                  <div className={styles.suggestion} key={link.id}>
                    <span>
                      Nota{" "}
                      {data.documents.find(
                        (item) => item.id === link.document_id,
                      )?.number ?? "a conferir"}
                      {" · "}
                      {dateLabel(link.movement_date)} ·{" "}
                      {link.movement_description}
                      {" · "}
                      {money(link.amount, link.currency)} ·{" "}
                      {link.state === "confirmed"
                        ? "Vínculo confirmado"
                        : "Origem alterada: revisar vínculo"}
                    </span>
                    <button
                      className="secondary-button"
                      disabled={busy}
                      onClick={() =>
                        void action(() =>
                          command(`/fiscal/links/${link.id}/undo`, {
                            expected_version: link.version,
                          }),
                        )
                      }
                    >
                      Desfazer vínculo
                    </button>
                  </div>
                ))}
              {!data.links.length && <p>Nenhum vínculo registrado.</p>}
            </>
          )}
          {mode !== "invoices" && (
            <>
              <h3>Classificar movimentos</h3>
              {visibleMovements.map((row) => (
                <MovementClassification
                  key={`${row.id}:${row.version}:${row.classification?.version ?? 0}`}
                  row={row}
                  categories={Object.fromEntries(
                    Object.entries(data.categories).filter(
                      ([category]) =>
                        category === "unclassified" ||
                        (category.startsWith("tax_")
                          ? data.features.taxes
                          : data.features.revenue),
                    ),
                  )}
                  busy={busy}
                  save={(category, details) =>
                    action(() =>
                      command(`/classifications/${row.id}`, {
                        category,
                        details,
                        expected_version: row.classification?.version ?? 0,
                        movement_version: row.version,
                      }),
                    )
                  }
                />
              ))}
              {!visibleMovements.length && (
                <p>Nenhum movimento acessível nesta página.</p>
              )}
            </>
          )}
          <div className={styles.actions}>
            {!initialMovementId && offset > 0 && (
              <button
                className="secondary-button"
                onClick={() => setOffset(Math.max(0, offset - 50))}
              >
                Página anterior
              </button>
            )}
            {!initialMovementId && data.next_offset !== null && (
              <button
                className="secondary-button"
                onClick={() => setOffset(data.next_offset!)}
              >
                Próxima página de movimentos
              </button>
            )}
            <button
              className="secondary-button"
              disabled={busy || data.truncated}
              onClick={() =>
                void action(
                  () =>
                    downloadFiscal(
                      clientId,
                      `export?${new URLSearchParams({ month, ...(account ? { account } : {}) })}`,
                      "fiscal.csv",
                    ),
                  false,
                )
              }
            >
              Exportar conferência CSV
            </button>
          </div>
          {busy && <p role="status">Salvando…</p>}
        </>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.metric}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MovementClassification({
  row,
  categories,
  busy,
  save,
}: {
  row: FiscalMovement;
  categories: Record<string, string>;
  busy: boolean;
  save: (category: string, details: Record<string, string>) => Promise<void>;
}) {
  const [category, setCategory] = useState(
    row.classification?.category ?? "unclassified",
  );
  const [competence, setCompetence] = useState(
    row.classification?.details.competence ?? "",
  );
  const [revenueCode, setRevenueCode] = useState(
    row.classification?.details.revenue_code ?? "",
  );
  return (
    <div className={styles.movement}>
      <div>
        <strong>{row.description}</strong>
        <p>
          {dateLabel(row.date)} · {money(row.amount, row.currency)}
        </p>
        <Link href={`/review/${row.source.batch_id}/${row.source.job_id}`}>
          Abrir extrato · {row.source.filename}
        </Link>
        {row.possible_duplicate && (
          <p className={styles.warning}>
            Possível sobreposição entre extratos.
          </p>
        )}
        {row.classification?.state === "review_required" && (
          <p className={styles.warning}>
            Movimento alterado: confirme novamente a classificação.
          </p>
        )}
        {row.tax_suggestion && (
          <p>
            Sugestão:{" "}
            {categories[row.tax_suggestion.category] ??
              row.tax_suggestion.category}
            . {row.tax_suggestion.evidence}
          </p>
        )}
      </div>
      <div className={styles.form}>
        <label>
          Categoria
          <select
            aria-label={`Categoria de ${row.description}`}
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            {Object.entries(categories).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {category.startsWith("tax_") && (
          <>
            <label>
              Competência informada
              <input
                type="month"
                value={competence}
                onChange={(event) => setCompetence(event.target.value)}
              />
            </label>
            <label>
              Código da receita
              <input
                maxLength={30}
                value={revenueCode}
                onChange={(event) => setRevenueCode(event.target.value)}
              />
            </label>
          </>
        )}
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() =>
            void save(
              category,
              category.startsWith("tax_")
                ? { competence, revenue_code: revenueCode }
                : {},
            )
          }
        >
          Confirmar classificação
        </button>
      </div>
    </div>
  );
}

function NoteReview({
  note,
  busy,
  save,
}: {
  note: FiscalNote;
  busy: boolean;
  save: (changes: Record<string, string | null>) => Promise<void>;
}) {
  const [number, setNumber] = useState(note.number ?? "");
  const [series, setSeries] = useState(note.series ?? "");
  const [issued, setIssued] = useState(note.issued_on ?? "");
  const [gross, setGross] = useState(note.gross_amount ?? "");
  const [net, setNet] = useState(note.net_amount ?? "");
  const [operation, setOperation] = useState(note.operation);
  const [status, setStatus] = useState(note.status);
  const [issuer, setIssuer] = useState(note.issuer.tax_id ?? "");
  const [recipient, setRecipient] = useState(note.recipient.tax_id ?? "");
  return (
    <details className={styles.note} open={note.review_required}>
      <summary>Conferir dados da nota</summary>
      {Object.entries(note.source.fields).length > 0 && (
        <p>
          Origem dos campos:{" "}
          {Object.entries(note.source.fields)
            .map(
              ([field, ref]) =>
                `${({ gross_amount: "valor bruto", net_amount: "valor líquido", issued_on: "emissão", number: "número", issuer_tax: "emitente", recipient_tax: "destinatário" } as Record<string, string>)[field] ?? "identificação"}: página ${ref.page}, linha ${ref.line}`,
            )
            .join("; ")}
        </p>
      )}
      <div className={styles.form}>
        <label>
          Número
          <input
            value={number}
            onChange={(event) => setNumber(event.target.value)}
          />
        </label>
        <label>
          Série
          <input
            value={series}
            onChange={(event) => setSeries(event.target.value)}
          />
        </label>
        <label>
          CPF/CNPJ do emitente
          <input
            value={issuer}
            onChange={(event) => setIssuer(event.target.value)}
          />
        </label>
        <label>
          CPF/CNPJ do destinatário
          <input
            value={recipient}
            onChange={(event) => setRecipient(event.target.value)}
          />
        </label>
        <label>
          Data de emissão
          <input
            type="date"
            value={issued}
            onChange={(event) => setIssued(event.target.value)}
          />
        </label>
        <label>
          Valor bruto
          <input
            inputMode="decimal"
            value={gross}
            onChange={(event) => setGross(event.target.value)}
          />
        </label>
        <label>
          Valor líquido informado
          <input
            inputMode="decimal"
            value={net}
            onChange={(event) => setNet(event.target.value)}
          />
        </label>
        <label>
          Natureza
          <select
            value={operation}
            onChange={(event) => setOperation(event.target.value)}
          >
            <option value="unknown">A identificar</option>
            <option value="sale">Venda ou serviço</option>
            <option value="return">Devolução</option>
            <option value="other">Outra operação</option>
          </select>
        </label>
        <label>
          Situação
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="reported">Informada no documento</option>
            <option value="cancelled">Cancelada</option>
            <option value="substituted">Substituída</option>
            <option value="duplicate">
              Duplicata documental — excluir dos totais
            </option>
          </select>
        </label>
      </div>
      <button
        className="secondary-button"
        disabled={busy}
        onClick={() =>
          void save({
            number,
            series: series || null,
            issued_on: issued,
            gross_amount: gross.replace(",", "."),
            net_amount: net ? net.replace(",", ".") : null,
            operation,
            status,
            issuer_tax_id: issuer,
            recipient_tax_id: recipient,
          })
        }
      >
        Confirmar dados da nota
      </button>
    </details>
  );
}
