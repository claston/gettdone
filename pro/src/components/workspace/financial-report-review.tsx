"use client";

import { useState } from "react";
import type { FinancialDocument } from "@/domain/workspace";
import { formatCurrency } from "@/domain/workspace";

function downloadUrl(base: string, format: string, account?: string[]) {
  const [path, query] = base.split("?");
  const params = new URLSearchParams(query);
  params.set("format", format);
  if (account) {
    ["bank_code", "bank_branch", "account_number"].forEach((key, index) =>
      params.set(key, account[index]),
    );
  }
  return `${path}?${params}`;
}

export function FinancialReportReview({
  report,
  downloadUrl: base,
  onSource,
}: {
  report: FinancialDocument;
  downloadUrl?: string;
  onSource?(eventId: string): void;
}) {
  const [account, setAccount] = useState(["", "", ""]);
  const [showAccount, setShowAccount] = useState(false);
  const failed = report.validations.filter(
    (item) => item.status === "failed",
  ).length;
  const statuses: Record<string, string> = {
    issued: "Emitido",
    voided: "Inutilizado",
    cancelled: "Cancelado",
    unknown: "Não identificado",
    confirmed: "Confirmado",
    paid: "Pago",
  };
  const types: Record<string, string> = {
    receipt: "Recebimento de título",
    investment_application: "Aplicação",
    investment_redemption: "Resgate",
    fiscal_document: "Documento fiscal",
  };
  const titles: Record<string, string> = {
    receivables_report: "Relatório de recebimentos",
    investment_statement: "Extrato de investimentos",
    fiscal_summary: "Resumo fiscal",
  };
  const canExportOfx =
    !!base && report.export.available_formats.includes("ofx");
  return (
    <section
      className="transactions-panel financial-report-panel"
      aria-label="Relatório especializado"
    >
      <div className="panel-toolbar financial-report-toolbar">
        <div className="financial-report-title">
          <h2>{titles[report.document_type] ?? "Relatório do documento"}</h2>
          <span>
            {report.origin ?? "Documento financeiro"} · {report.events.length}{" "}
            {report.events.length === 1 ? "evento" : "eventos"}
          </span>
        </div>
        {base && (
          <div className="financial-report-downloads">
            {report.export.available_formats
              .filter((format) => format !== "ofx")
              .map((format) => (
                <a
                  className="secondary-button small"
                  key={format}
                  href={downloadUrl(base, format)}
                  download
                >
                  Baixar {format === "xlsx" ? "Excel" : format.toUpperCase()}
                </a>
              ))}
            {canExportOfx && (
              <button
                type="button"
                className="secondary-button small"
                aria-expanded={showAccount}
                onClick={() => setShowAccount((value) => !value)}
              >
                Exportar OFX
              </button>
            )}
          </div>
        )}
      </div>
      <div className="financial-report-notice" role="status">
        <p>{report.notice}</p>
        {failed > 0 && (
          <p>
            {failed} validação(ões) com diferença. Confira os totais no Excel.
          </p>
        )}
        <details>
          <summary>Dados disponíveis no Excel</summary>
          <p>
            Os campos de origem são preservados. Datas, impostos, posições e
            situações estão disponíveis no Excel.
          </p>
        </details>
      </div>
      {base && canExportOfx && showAccount && (
        <fieldset className="financial-destination">
          <legend>Conta de destino dos recebimentos para exportação OFX</legend>
          <div className="financial-destination-fields">
            {["Banco de destino", "Agência de destino", "Conta de destino"].map(
              (label, index) => (
                <label key={label}>
                  {label}
                  <input
                    aria-label={label}
                    inputMode="numeric"
                    autoComplete="off"
                    value={account[index]}
                    onChange={(event) =>
                      setAccount((current) =>
                        current.map((value, position) =>
                          position === index
                            ? event.target.value.replace(/\D/g, "")
                            : value,
                        ),
                      )
                    }
                  />
                </label>
              ),
            )}
          </div>
          {account.every(Boolean) && (
            <a
              className="secondary-button"
              href={downloadUrl(base, "ofx", account)}
              download
            >
              Baixar OFX
            </a>
          )}
        </fieldset>
      )}
      <div className="table-scroll">
        <table
          className="financial-events-table"
          aria-label="Eventos do relatório"
        >
          <thead>
            <tr>
              {["Data", "Evento", "Valor", "Situação", "Origem"].map(
                (label) => (
                  <th key={label} scope="col">
                    {label}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {report.events.map((event) => {
              const rawDate =
                event.dates.payment ??
                event.dates.redemption ??
                event.dates.application ??
                event.dates.issue;
              return (
                <tr key={event.event_id}>
                  <td className="financial-event-date">
                    {rawDate?.split("-").reverse().join("/") ?? "—"}
                  </td>
                  <td className="financial-event-description">
                    <span className="financial-event-type">
                      {types[event.event_type] ?? "Evento financeiro"}
                    </span>
                    <span className="financial-event-text">
                      {event.description}
                    </span>
                    {event.reference && (
                      <span className="financial-event-reference">
                        Referência: {event.reference}
                      </span>
                    )}
                  </td>
                  <td className="financial-event-amount">
                    {formatCurrency(Number(event.amount))}
                  </td>
                  <td>
                    <span
                      className="financial-event-status"
                      data-status={event.status}
                    >
                      {statuses[event.status] ?? "Não identificado"}
                    </span>
                  </td>
                  <td>
                    {onSource && event.source_refs.length > 0 ? (
                      <button
                        className="financial-source-link"
                        aria-label={`Ver origem do evento ${event.description}`}
                        onClick={() => onSource(event.event_id)}
                      >
                        Página {event.source_refs[0]?.page}
                      </button>
                    ) : (
                      event.source_refs
                        .map((ref) => `Pág. ${ref.page}, linha ${ref.line}`)
                        .join("; ") || "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {report.events.length === 0 && (
          <div className="financial-report-empty">
            <strong>Sem movimentos neste período</strong>
            <p>
              As posições e os demais dados do documento estão disponíveis no
              Excel.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
