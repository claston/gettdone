"use client";

import type { FinancialDocument } from "@/domain/workspace";

export function DocumentEvidenceReview({
  report,
}: {
  report: FinancialDocument;
}) {
  const invoice = report.document_type === "fiscal_invoice";
  return (
    <section
      className="transactions-panel"
      aria-label="Conferência do documento"
    >
      <div className="panel-toolbar">
        <h2>{invoice ? "Conferir nota fiscal" : "Conferir comprovante"}</h2>
      </div>
      <div className="financial-report-notice">
        <p>{report.notice}</p>
        {invoice && (
          <p>
            A verificação fiscal e a conferência dos itens serão tratadas
            separadamente.
          </p>
        )}
      </div>
      {(report.observations?.length ?? 0) > 0 ? (
        <div className="table-scroll">
          <table className="financial-events-table">
            <thead>
              <tr>
                <th scope="col">Campo</th>
                <th scope="col">Informação lida</th>
                <th scope="col">Origem</th>
              </tr>
            </thead>
            <tbody>
              {report.observations?.map((field, index) => (
                <tr key={index}>
                  <td>{String(field.attributes.label ?? "Informação")}</td>
                  <td>
                    {String(field.attributes.value ?? "Não identificado")}
                  </td>
                  <td>
                    {field.source_refs
                      .map((ref) => `Pág. ${ref.page}, linha ${ref.line}`)
                      .join("; ") || "Confira o original"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p>
          Não foi possível identificar os campos com segurança. Confira o
          documento original ao lado.
        </p>
      )}
    </section>
  );
}
