"use client";

import Link from "next/link";
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
import { FiscalWorkspace } from "../financial/fiscal-workspace";
import { NewConversionForm } from "../new-conversion-form";
import styles from "./documents.module.css";
import {
  documentCategories,
  documentsUrl,
} from "@/domain/documents-navigation";

export function DocumentsPage({
  clientId = "",
  category = "all",
  batchId = "",
  service = documentsService,
  organization = organizationService,
  financialCenterVisible = false,
}: {
  clientId?: string;
  category?: DocumentCategory;
  batchId?: string;
  service?: DocumentsService;
  organization?: OrganizationService;
  financialCenterVisible?: boolean;
}) {
  const router = useRouter();
  const [catalog, setCatalog] = useState<OrganizationCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [cursors, setCursors] = useState<string[]>([""]);
  const [upload, setUpload] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState("");
  const [operationError, setOperationError] = useState("");
  const [snapshot, setSnapshot] = useState<{
    key: string;
    data?: DocumentData;
    error?: string;
  } | null>(null);
  const client = catalog?.clients.find((c) => c.id === clientId);
  const scoped = Boolean(clientId && clientId !== "unassigned");
  const query = new URLSearchParams({ category });
  if (batchId) query.set("batch_id", batchId);
  else if (clientId === "unassigned") query.set("unassigned", "true");
  else if (clientId) query.set("client_id", clientId);
  if (cursors.at(-1)) query.set("cursor", cursors.at(-1)!);
  const request = query.toString();
  const key = request;
  const current = snapshot?.key === key ? snapshot : null;
  const returnTo = documentsUrl(clientId, category, batchId);
  const [month] = useState(() =>
    new Intl.DateTimeFormat("sv-SE", {
      timeZone: "America/Sao_Paulo",
      year: "numeric",
      month: "2-digit",
    }).format(new Date()),
  );

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
          <header className="page-title">
            <div>
              <h1>{client?.name ?? "Documentos do cliente"}</h1>
              <p>{client?.tax_id}</p>
            </div>
          </header>
          <nav className={styles.tabs} aria-label="Áreas do cliente">
            <Link href={documentsUrl(clientId)} aria-current="page">
              Documentos
            </Link>
            {financialCenterVisible && (
              <Link href={`/financial/${encodeURIComponent(clientId)}`}>
                Central Financeira
              </Link>
            )}
          </nav>
        </>
      )}
      <header className="page-title">
        <div>
          {scoped ? <h2>Documentos</h2> : <h1>Documentos</h1>}
          <p>
            {scoped
              ? "Consulte e confira os documentos deste cliente."
              : "Consulte os documentos do escritório e organize os arquivos por cliente."}
          </p>
        </div>
        <div className={styles.actions}>
          <button
            className="secondary-button"
            disabled={refreshing || upload}
            onClick={() => void refresh()}
          >
            {refreshing ? "Atualizando…" : "Atualizar documentos"}
          </button>
          <button
            className="primary-button"
            onClick={() => setUpload((value) => !value)}
            aria-expanded={upload}
          >
            Importar documentos
          </button>
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
        <div className={styles.fields}>
          <label>
            Cliente
            <select
              value={clientId}
              onChange={(e) =>
                router.push(documentsUrl(e.target.value, category))
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
      )}
      {batchId && (
        <div className={styles.notice}>
          <p>
            Arquivos deste envio. Confira o titular e a associação de cada
            documento{client ? ` a ${client.name}` : ""}. Importar não confirma
            conciliação.
          </p>
          <Link href={documentsUrl(clientId, category)}>
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
        <section className={styles.panel} aria-label="Importação de documentos">
          <h2>Qual documento deseja importar?</h2>
          <p>
            Envie extratos e comprovantes para consultar os dados e conferir a
            leitura.
          </p>
          <div className={styles.actions}>
            {client && (
              <Link
                href={`${documentsUrl(clientId, "invoices")}#notas-fiscais`}
              >
                Importar nota fiscal XML ou PDF
              </Link>
            )}
            <button
              className="secondary-button"
              onClick={() => setUpload(false)}
            >
              Fechar importação
            </button>
          </div>
          {!scoped && (
            <p>Para notas fiscais, selecione o cliente antes de importar.</p>
          )}
          <NewConversionForm
            importContext={{ clientId: client?.id, clientName: client?.name }}
          />
        </section>
      )}
      <nav className={styles.filters} aria-label="Tipos de documento">
        {Object.entries(documentCategories).map(([value, label]) => (
          <Link
            key={value}
            href={documentsUrl(clientId, value as DocumentCategory, batchId)}
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
          {(category === "all" || category === "invoices") && (
            <p>
              Arquivos recebidos pelo Conversor ou pela importação de
              documentos:
            </p>
          )}
          <div className={styles.list}>
            {current.data?.items.map((item) => (
              <DocumentRow
                key={`${item.job_id}:${item.context.version}`}
                item={item}
                catalog={catalog}
                organization={organization}
                client={batchId ? client : undefined}
                returnTo={returnTo}
                onSaved={() => setAttempt((a) => a + 1)}
              />
            ))}
          </div>
          {!current.data?.items.length && (
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
      {!batchId && (category === "all" || category === "invoices") && (
        <section id="notas-fiscais" aria-label="Notas fiscais importadas">
          {!client && <h2>Notas fiscais importadas</h2>}
          {client ? (
            <FiscalWorkspace
              key={client.id}
              clientId={client.id}
              month={month}
              account=""
              mode="invoices"
              documentsOnly
            />
          ) : (
            <p>
              Selecione um cliente para consultar e importar suas notas fiscais.
            </p>
          )}
        </section>
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
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
