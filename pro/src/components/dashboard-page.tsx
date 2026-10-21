"use client";

import { ArrowRight, FileStack, LoaderCircle, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  batchHistoryService,
  type BatchHistoryPage,
  type BatchHistoryService,
} from "@/data/batch-history-service";

const statuses: Record<string, string> = {
  uploading: "Aguardando envio",
  queued: "Na fila",
  processing: "Em processamento",
  completed: "Concluído",
  completed_with_errors: "Concluído com falhas",
  failed: "Falhou",
  expired: "Expirado",
};
const dateLabel = (value: string) =>
  new Date(value).toLocaleString("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  });

export function DashboardPage({ service }: { service?: BatchHistoryService }) {
  const activeService = service ?? batchHistoryService;
  const [cursors, setCursors] = useState<Array<string | undefined>>([
    undefined,
  ]);
  const cursor = cursors.at(-1);
  const [attempt, setAttempt] = useState(0);
  const request = useMemo(
    () => ({ service: activeService, cursor, attempt }),
    [activeService, cursor, attempt],
  );
  const [result, setResult] = useState<{
    request: typeof request;
    page: BatchHistoryPage | null;
    error: string | null;
  } | null>(null);
  const current = result?.request === request ? result : null;
  const page = current?.page ?? activeService.getCachedPage?.(cursor) ?? null;
  const loading = !current && !page;
  const error = current?.error ?? null;

  useEffect(() => {
    const abort = new AbortController();
    void request.service
      .list(request.cursor, abort.signal)
      .then((page) => {
        if (!abort.signal.aborted) setResult({ request, page, error: null });
      })
      .catch((caught: unknown) => {
        if (!abort.signal.aborted)
          setResult({
            request,
            page: null,
            error:
              caught instanceof Error
                ? caught.message
                : "Não foi possível carregar os lotes.",
          });
      });
    return () => abort.abort();
  }, [request]);

  return (
    <main className="standard-page dashboard-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">OFX Simples PRO</span>
          <h1>Visão geral</h1>
          <p>Retome a revisão dos seus lotes de conversão.</p>
        </div>
        <Link href="/conversions" className="primary-button">
          <Plus size={16} /> Nova conversão
        </Link>
      </div>
      <section className="batch-history-card" aria-label="Seus lotes">
        <h2>
          <FileStack size={20} /> Seus lotes
        </h2>
        {!current && page && (
          <p className="history-state" role="status">
            Atualizando lotes…
          </p>
        )}
        {error && page && (
          <p className="history-state" role="alert">
            {error}
          </p>
        )}
        {loading ? (
          <p className="history-state" role="status">
            <LoaderCircle className="spin" size={18} /> Carregando lotes…
          </p>
        ) : error && !page ? (
          <div className="history-state" role="alert">
            <p>{error}</p>
            <button
              className="secondary-button"
              onClick={() => setAttempt((value) => value + 1)}
            >
              Tentar novamente
            </button>
          </div>
        ) : page?.items.length ? (
          <>
            <div className="history-table-scroll">
              <table
                className="batch-history-table"
                aria-label="Lotes de conversão"
              >
                <thead>
                  <tr>
                    <th>Criado em</th>
                    <th>Arquivos</th>
                    <th>Situação</th>
                    <th>Concluídos</th>
                    <th>
                      <span className="sr-only">Ações</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        {dateLabel(item.createdAt)}
                        <small>
                          {item.isExpired
                            ? "Arquivos expirados"
                            : `Disponível até ${dateLabel(item.expiresAt)}`}
                        </small>
                      </td>
                      <td>{item.fileCount}</td>
                      <td>
                        <span
                          className={`history-status ${item.isExpired ? "expired" : ""}`}
                        >
                          {statuses[item.status] ?? "Em processamento"}
                        </span>
                      </td>
                      <td>
                        {item.completedCount} de {item.fileCount}
                        {item.failedCount > 0 && (
                          <small>{item.failedCount} com falha</small>
                        )}
                        {item.pendingCount > 0 && (
                          <small>{item.pendingCount} pendente(s)</small>
                        )}
                      </td>
                      <td>
                        {item.reviewUrl ? (
                          <Link
                            href={item.reviewUrl}
                            className="secondary-button small"
                          >
                            Abrir lote <ArrowRight size={14} />
                          </Link>
                        ) : (
                          <span className="history-unavailable">
                            Indisponível
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <nav
              className="history-pagination"
              aria-label="Paginação dos lotes"
            >
              <button
                className="secondary-button small"
                disabled={cursors.length === 1}
                onClick={() => setCursors((current) => current.slice(0, -1))}
              >
                Anterior
              </button>
              <span>Página {cursors.length}</span>
              <button
                className="secondary-button small"
                disabled={!page.nextCursor}
                onClick={() =>
                  setCursors((current) => [...current, page.nextCursor!])
                }
              >
                Próxima
              </button>
            </nav>
          </>
        ) : (
          <div className="history-state">
            <p>Nenhum lote de conversão encontrado.</p>
            <Link href="/conversions" className="primary-button">
              <Plus size={16} /> Criar conversão real
            </Link>
          </div>
        )}
      </section>
      <Link
        href="/demo/review/batch-setembro-2026"
        className="history-demo-link"
      >
        Ver demonstração com dados fictícios <ArrowRight size={14} />
      </Link>
    </main>
  );
}
