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
    investment_application: "Aplicação CDB",
    investment_redemption: "Resgate CDB",
    fiscal_document: "Documento fiscal",
  };
  return (
    <section className="transaction-grid" aria-label="Relatório especializado">
      <div className="page-sync-notice" role="status">
        <p>{report.notice}</p>
        {failed > 0 && (
          <p>
            {failed} validação(ões) com diferença. Confira os totais no Excel.
          </p>
        )}
        <p>
          Os campos de origem são preservados. Datas, impostos, posições e
          situações estão disponíveis no Excel.
        </p>
      </div>
      {base && (
        <div className="header-actions">
          {report.export.available_formats
            .filter((format) => format !== "ofx")
            .map((format) => (
              <a
                className="secondary-button"
                key={format}
                href={downloadUrl(base, format)}
                download
              >
                Baixar {format === "xlsx" ? "Excel" : format.toUpperCase()}
              </a>
            ))}
        </div>
      )}
      {base && report.export.available_formats.includes("ofx") && (
        <fieldset>
          <legend>Conta de destino dos recebimentos para exportação OFX</legend>
          {["Banco de destino", "Agência de destino", "Conta de destino"].map(
            (label, index) => (
              <label key={label}>
                {label}
                <input
                  aria-label={label}
                  inputMode="numeric"
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
        <table aria-label="Eventos do relatório">
          <thead>
            <tr>
              {[
                "Data",
                "Evento",
                "Valor",
                "Situação",
                "Referência",
                "Origem",
              ].map((label) => (
                <th key={label}>{label}</th>
              ))}
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
                  <td>{rawDate?.split("-").reverse().join("/")}</td>
                  <td>
                    {types[event.event_type] ?? event.event_type} —{" "}
                    {event.description}
                  </td>
                  <td>{formatCurrency(Number(event.amount))}</td>
                  <td>{statuses[event.status] ?? event.status}</td>
                  <td>{event.reference ?? "—"}</td>
                  <td>
                    {onSource ? (
                      <button
                        className="row-action"
                        onClick={() => onSource(event.event_id)}
                      >
                        Página {event.source_refs[0]?.page}
                      </button>
                    ) : (
                      event.source_refs
                        .map((ref) => `Pág. ${ref.page}, linha ${ref.line}`)
                        .join("; ")
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {report.events.length === 0 && (
        <p>Sem movimentos; posições disponíveis no Excel.</p>
      )}
    </section>
  );
}
