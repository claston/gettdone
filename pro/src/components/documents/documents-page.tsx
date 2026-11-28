"use client";

import Link from "next/link";
import { OfficeDocumentControls, OfficePackage } from "./office-controls";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  organizationService,
  type OrganizationCatalog,
  type OrganizationService,
  type OrganizationClient,
} from "@/data/organization-service";
import {
  documentsService,
  type DocumentsService,
  type DocumentsPage as DocumentData,
  type DocumentCategory,
  type DocumentItem,
} from "@/data/documents-service";
import { receivedService } from "@/data/received-statements-service";
import { ConversionOrganization } from "../organization/conversion-organization";
import {
  documentAvailability,
  useAvailableImports,
} from "../use-available-imports";
import { NewConversionForm } from "../new-conversion-form";
import { DocumentConference } from "./document-conference";
import { MonthFilter } from "./month-filter";
import conferenceStyles from "./conference.module.css";
import styles from "./documents.module.css";
import {
  documentCategories,
  documentsUrl,
} from "@/domain/documents-navigation";

export function DocumentsPage({
  clientId = "",
  category = "all",
  batchId = "",
  month = "",
  accountId = "",
  service = documentsService,
  organization = organizationService,
  financialCenterVisible = false,
  initialView,
}: {
  clientId?: string;
  category?: DocumentCategory;
  batchId?: string;
  month?: string;
  accountId?: string;
  service?: DocumentsService;
  organization?: OrganizationService;
  financialCenterVisible?: boolean;
  initialView?: "conference" | "files" | "delivery";
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<string[]>([]);
  const [catalog, setCatalog] = useState<OrganizationCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [cursors, setCursors] = useState<string[]>([""]);
  const [upload, setUpload] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [conferenceRevision, setConferenceRevision] = useState(0);
  const [notice, setNotice] = useState("");
  const [operationError, setOperationError] = useState("");
  const [snapshot, setSnapshot] = useState<{
    key: string;
    data?: DocumentData;
    error?: string;
  } | null>(null);
  const client = catalog?.clients.find((c) => c.id === clientId);
  const scoped = Boolean(clientId && clientId !== "unassigned");
  const canConfer = scoped && financialCenterVisible && month !== "unknown";
  const [view, setView] = useState<"conference" | "files" | "delivery">(
    initialView ??
      (canConfer && !batchId && category === "all" ? "conference" : "files"),
  );
  const activeView = view === "conference" && !canConfer ? "files" : view;
  const [selectedDocument, setSelectedDocument] = useState("");
  const [comparisonDocument, setComparisonDocument] =
    useState<DocumentItem | null>(null);
  const [fileDetail, setFileDetail] = useState(false);
  const query = new URLSearchParams({ category });
  if (month) query.set("month", month);
  if (accountId) query.set("account_id", accountId);
  if (batchId) query.set("batch_id", batchId);
  else if (clientId === "unassigned") query.set("unassigned", "true");
  else if (clientId) query.set("client_id", clientId);
  if (cursors.at(-1)) query.set("cursor", cursors.at(-1)!);
  const request = query.toString();
  const key = request;
  const current = snapshot?.key === key ? snapshot : null;
  const items = useAvailableImports(current?.data?.items, documentAvailability);
  const returnTo = documentsUrl(
    clientId,
    category,
    batchId,
    month,
    accountId,
    activeView,
  );
  const selectedFile =
    items.find((item) => item.job_id === selectedDocument) ?? items[0];

  useEffect(() => {
    const abort = new AbortController();
    void organization
      .catalog(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) {
          setCatalog(value);
          setCatalogError("");
        }
      })
      .catch(() => {
        if (!abort.signal.aborted) {
          setCatalog(null);
          setCatalogError(
            "Não foi possível carregar os clientes. Tente novamente.",
          );
        }
      });
    return () => abort.abort();
  }, [organization, attempt]);
  useEffect(() => {
    const abort = new AbortController();
    void service
      .list(request, abort.signal)
      .then((data) => {
        if (!abort.signal.aborted) setSnapshot({ key, data });
      })
      .catch((caught) => {
        if (!abort.signal.aborted)
          setSnapshot({
            key,
            error:
              caught instanceof Error
                ? caught.message
                : "Não foi possível consultar os documentos.",
          });
      });
    return () => abort.abort();
  }, [service, request, key, attempt]);
  useEffect(() => {
    const update = () => {
      if (!upload && document.visibilityState === "visible")
        setAttempt((a) => a + 1);
    };
    window.addEventListener("focus", update);
    const live = current?.data?.items.filter((item) => !item.is_expired) ?? [];
    const remaining = live
      .map((item) => Date.parse(item.expires_at) - Date.now())
      .filter((value) => Number.isFinite(value) && value > 0);
    const timer = remaining.length
      ? window.setTimeout(
          () => setAttempt((a) => a + 1),
          Math.min(...remaining, 2147483647),
        )
      : undefined;
    const poll =
      !upload &&
      live.some((item) =>
        ["uploaded", "queued", "running", "retrying"].includes(item.status),
      )
        ? window.setTimeout(update, 5000)
        : undefined;
    return () => {
      window.removeEventListener("focus", update);
      window.clearTimeout(timer);
      window.clearTimeout(poll);
    };
  }, [current, upload]);
  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setNotice("");
    setOperationError("");
    try {
      const result = await receivedService.refresh();
      if (
        result &&
        (result.pending || result.review_required || result.unavailable)
      )
        setNotice(
          "Alguns extratos ainda não estão na Central Financeira. Confira os documentos e seus avisos antes de usar os totais.",
        );
    } catch (caught) {
      setOperationError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível atualizar os documentos.",
      );
    } finally {
      setAttempt((a) => a + 1);
      setConferenceRevision((value) => value + 1);
      setRefreshing(false);
    }
  };
  return (
    <main className={`standard-page ${styles.page}`}>
      {scoped && (
        <>
          <nav className={styles.breadcrumb} aria-label="Localização">
            <Link href="/dashboard">Clientes</Link>
            <span>/</span>
            <span>{client?.name ?? "Cliente selecionado"}</span>
          </nav>
          <header className={styles.clientHeading}>
            <div>
              <h1>{client?.name ?? "Documentos do cliente"}</h1>
              <p>{client?.tax_id}</p>
            </div>
          </header>
          <nav className={styles.tabs} aria-label="Áreas do cliente">
            {canConfer && (
              <button
                aria-pressed={activeView === "conference"}
                onClick={() => setView("conference")}
              >
                Conferência
              </button>
            )}
            <button
              aria-pressed={activeView === "files"}
              onClick={() => setView("files")}
            >
              Arquivos
            </button>
            <button
              aria-pressed={activeView === "delivery"}
              onClick={() => setView("delivery")}
            >
              Entrega
            </button>
            {financialCenterVisible && (
              <Link href={`/financial/${encodeURIComponent(clientId)}`}>
                Central Financeira
              </Link>
            )}
          </nav>
        </>
      )}
      <header
        className={
          activeView === "conference" ? styles.conferenceToolbar : "page-title"
        }
      >
        {activeView !== "conference" && (
          <div>
            {scoped ? (
              <h2>
                {activeView === "delivery"
                  ? "Preparar entrega"
                  : "Arquivos recebidos"}
              </h2>
            ) : (
              <h1>Documentos</h1>
            )}
            <p>
              {scoped
                ? activeView === "delivery"
                  ? "Selecione os arquivos e confira o conteúdo antes de baixar."
                  : "Selecione um arquivo para consultar, conferir ou organizar."
                : "Consulte os documentos do escritório e organize os arquivos por cliente."}
            </p>
          </div>
        )}
        <div className={styles.actions}>
          <button
            className="secondary-button"
            disabled={refreshing || upload}
            onClick={() => void refresh()}
            aria-label={
              refreshing ? "Atualizando documentos" : "Atualizar documentos"
            }
          >
            {refreshing ? "Atualizando…" : "Atualizar"}
          </button>
          {!upload && (
            <button
              className="primary-button"
              disabled={scoped && !client}
              onClick={() => setUpload(true)}
            >
              Importar documentos
            </button>
          )}
        </div>
      </header>
      {catalogError && (
        <p role="alert">
          {catalogError}{" "}
          <button
            className="secondary-button"
            onClick={() => setAttempt((a) => a + 1)}
          >
            Tentar novamente
          </button>
        </p>
      )}
      {catalog && (
        <details className={styles.clientPicker} open={!scoped}>
          <summary>{scoped ? "Trocar cliente" : "Selecionar cliente"}</summary>
          <div className={styles.fields}>
            <label>
              Cliente
              <select
                value={clientId}
                disabled={upload}
                onChange={(e) =>
                  router.push(documentsUrl(e.target.value, category, "", month))
                }
              >
                <option value="">Todos os clientes</option>
                <option value="unassigned">Sem cliente associado</option>
                {catalog.clients.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </details>
      )}
      <div className={`${styles.fields} ${styles.workFilters}`}>
        {scoped && (
          <label>
            Conta{" "}
            <select
              value={accountId}
              onChange={(e) =>
                router.push(
                  documentsUrl(
                    clientId,
                    category,
                    batchId,
                    month,
                    e.target.value,
                    activeView,
                  ),
                )
              }
            >
              <option value="">Todas as contas</option>
              {catalog?.accounts
                .filter((account) => account.client_id === clientId)
                .map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
            </select>
          </label>
        )}
        <MonthFilter
          month={month}
          onChange={(value) =>
            router.push(
              documentsUrl(
                clientId,
                category,
                batchId,
                value,
                accountId,
                activeView,
              ),
            )
          }
          unknownHref={documentsUrl(
            clientId,
            category,
            batchId,
            "unknown",
            accountId,
            "files",
          )}
          allHref={documentsUrl(
            clientId,
            category,
            batchId,
            "",
            accountId,
            activeView,
          )}
          allLabel={
            activeView === "conference" ? "Mês mais recente" : "Todos os meses"
          }
        />
      </div>
      {batchId && (
        <div className={styles.notice}>
          <p>
            Arquivos deste envio. Confira o titular e o cliente associado a cada
            documento. Importar não confirma conciliação.
          </p>
          <Link href={documentsUrl(clientId, category, "", month, accountId)}>
            Ver todos os documentos{client ? " do cliente" : ""}
          </Link>
        </div>
      )}
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {operationError && <p role="alert">{operationError}</p>}
      {upload && (
        <NewConversionForm
          importContext={{ clientId: client?.id, clientName: client?.name }}
          onClose={() => setUpload(false)}
        />
      )}
      {activeView === "conference" && (
        <DocumentConference
          key={`${clientId}:${month}:${accountId}:${comparisonDocument?.job_id ?? ""}:${conferenceRevision}`}
          clientId={clientId}
          month={month}
          accountId={accountId}
          returnTo={returnTo}
          initialDocument={comparisonDocument}
        />
      )}
      {activeView !== "conference" && (
        <>
          <nav className={styles.filters} aria-label="Tipos de documento">
            {Object.entries(documentCategories).map(([value, label]) => (
              <Link
                key={value}
                href={documentsUrl(
                  clientId,
                  value as DocumentCategory,
                  batchId,
                  month,
                  accountId,
                  activeView,
                )}
                aria-current={category === value ? "page" : undefined}
              >
                {label}
              </Link>
            ))}
          </nav>
          {!current ? (
            <p role="status">Carregando documentos…</p>
          ) : current.error ? (
            <div role="alert">
              <p>{current.error}</p>
              <button
                className="secondary-button"
                onClick={() => setAttempt((a) => a + 1)}
              >
                Tentar novamente
              </button>
            </div>
          ) : (
            <>
              {activeView === "delivery" ? (
                <div className={styles.list}>
                  {items.map((item) => (
                    <div key={`${item.job_id}:${item.context.version}`}>
                      {scoped && (
                        <label>
                          <input
                            type="checkbox"
                            checked={selected.includes(item.job_id)}
                            disabled={
                              !selected.includes(item.job_id) &&
                              selected.length >= 30
                            }
                            onChange={(e) =>
                              setSelected((ids) =>
                                e.target.checked
                                  ? [...ids, item.job_id]
                                  : ids.filter((id) => id !== item.job_id),
                              )
                            }
                          />
                          Selecionar {item.filename} para o pacote
                        </label>
                      )}
                      <p className={styles.type}>
                        {item.filename} · {documentCategories[item.category]}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div
                  className={`${conferenceStyles.layout} ${fileDetail ? conferenceStyles.detailOpen : ""}`}
                >
                  <div
                    className={conferenceStyles.list}
                    aria-label="Arquivos recebidos"
                  >
                    {items.map((item) => (
                      <button
                        className={conferenceStyles.row}
                        key={item.job_id}
                        aria-pressed={selectedFile?.job_id === item.job_id}
                        onClick={() => {
                          setSelectedDocument(item.job_id);
                          setFileDetail(true);
                        }}
                      >
                        <span className={conferenceStyles.meta}>
                          {documentCategories[item.category]}
                        </span>
                        <strong>{item.filename}</strong>
                        <span className={conferenceStyles.meta}>
                          {item.movement_start_date
                            ? `Data identificada: ${item.movement_start_date.split("-").reverse().join("/")}`
                            : "Período a confirmar"}
                        </span>
                        <span className={conferenceStyles.badge}>
                          {item.status === "completed"
                            ? item.is_bank
                              ? item.bank_reviewed
                                ? "Revisão bancária concluída"
                                : "Revisão bancária pendente"
                              : item.verification?.status === "verified"
                                ? "Conferido manualmente"
                                : item.verification?.status === "attention"
                                  ? "Precisa de atenção"
                                  : item.verification?.status ===
                                      "unprocessable"
                                    ? "Não processável"
                                    : "A conferir"
                            : item.status === "failed"
                              ? "Leitura não concluída"
                              : "Em processamento"}
                        </span>
                      </button>
                    ))}
                  </div>
                  {selectedFile && (
                    <div className={conferenceStyles.detail}>
                      <button
                        className={`secondary-button ${conferenceStyles.back}`}
                        onClick={() => setFileDetail(false)}
                      >
                        Voltar aos arquivos
                      </button>
                      {canConfer &&
                        ["receipts", "invoices"].includes(
                          selectedFile.category,
                        ) &&
                        selectedFile.context.client_id === clientId &&
                        selectedFile.status === "completed" && (
                          <button
                            className="primary-button"
                            onClick={() => {
                              setComparisonDocument(selectedFile);
                              setView("conference");
                            }}
                          >
                            Comparar com movimentação
                          </button>
                        )}
                      <DocumentRow
                        key={`${selectedFile.job_id}:${selectedFile.context.version}`}
                        item={selectedFile}
                        catalog={catalog}
                        organization={organization}
                        client={batchId ? client : undefined}
                        returnTo={returnTo}
                        onSaved={() => setAttempt((value) => value + 1)}
                      />
                    </div>
                  )}
                </div>
              )}
              {scoped && activeView === "delivery" && (
                <div className={styles.panel}>
                  <OfficePackage
                    key={`${request}:${selected.join(",")}`}
                    clientId={clientId}
                    month={month}
                    jobs={selected}
                  />
                </div>
              )}
              {!items.length && (
                <p>
                  {current.data?.next_cursor
                    ? "Nenhum documento com esses filtros nesta página. Consulte a próxima página."
                    : "Nenhum documento encontrado nesta lista com os filtros selecionados."}
                </p>
              )}
              <div className={styles.pagination}>
                {cursors.length > 1 && (
                  <button
                    className="secondary-button"
                    onClick={() => setCursors((values) => values.slice(0, -1))}
                  >
                    Página anterior
                  </button>
                )}
                {current.data?.next_cursor && (
                  <button
                    className="secondary-button"
                    onClick={() =>
                      setCursors((values) => [
                        ...values,
                        current.data!.next_cursor!,
                      ])
                    }
                  >
                    Próxima página
                  </button>
                )}
              </div>
            </>
          )}
        </>
      )}
    </main>
  );
}

function DocumentRow({
  item,
  catalog,
  organization,
  client,
  returnTo,
  onSaved,
}: {
  item: DocumentItem;
  catalog: OrganizationCatalog | null;
  organization: OrganizationService;
  client?: OrganizationClient;
  returnTo: string;
  onSaved(): void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [expiredByClock, setExpiredByClock] = useState(
    () => Date.parse(item.expires_at) <= Date.now(),
  );
  useEffect(() => {
    const delay = Date.parse(item.expires_at) - Date.now();
    if (!Number.isFinite(delay) || item.is_expired) return;
    const timer = window.setTimeout(
      () => setExpiredByClock(true),
      Math.max(0, Math.min(delay, 2147483647)),
    );
    return () => window.clearTimeout(timer);
  }, [item.expires_at, item.is_expired]);
  const expired = item.is_expired || expiredByClock;
  return (
    <div className={styles.document}>
      <p className={styles.type}>{documentCategories[item.category]}</p>
      <ConversionOrganization
        item={
          expired
            ? { ...item, is_expired: true, review_url: null, status: "expired" }
            : item.is_bank === false
              ? { ...item, movement_start_date: null, movement_end_date: null }
              : item
        }
        catalog={catalog ?? { clients: [], accounts: [], periods: [] }}
        service={organization}
        available={Boolean(catalog)}
        showOrganization
        documentMode
        returnTo={returnTo}
        onSaved={onSaved}
        onCatalogChanged={async () => onSaved()}
      />
      {client &&
        item.context.client_id &&
        item.context.client_id !== client.id && (
          <p role="status">
            Este arquivo está associado a outro cliente. Confira o titular antes
            de alterar a associação em Organizar.
          </p>
        )}
      {client &&
        !item.context.client_id &&
        !expired &&
        item.review_url &&
        item.status === "completed" && (
          <div className={styles.notice}>
            <p>
              Confirme o titular no documento antes de associá-lo a este
              cliente.
            </p>
            <button
              className="secondary-button"
              disabled={busy}
              onClick={async () => {
                if (busy) return;
                setBusy(true);
                setError("");
                try {
                  await receivedService.confirm(item.job_id, {
                    client_id: client.id,
                    name: client.name,
                    tax_id: client.tax_id,
                    expected_version: item.context.version,
                  });
                  onSaved();
                } catch (caught) {
                  setError(
                    caught instanceof Error
                      ? caught.message
                      : "Não foi possível associar o documento.",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Associando…" : "Confirmar associação ao cliente"}
            </button>
          </div>
        )}
      {!expired && (
        <OfficeDocumentControls
          key={`${item.source}:${item.verification?.version}`}
          item={item}
          onSaved={onSaved}
        />
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
