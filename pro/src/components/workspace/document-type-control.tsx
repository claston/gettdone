"use client";

import { useState } from "react";
import type { Conversion } from "@/domain/workspace";

const labels: Record<string, string> = {
  bank_statement: "Extrato",
  bank_receipt: "Comprovante",
  fiscal_invoice: "Nota fiscal",
  unknown: "Não identificado",
};

export function DocumentTypeControl({
  conversion,
  disabled,
  onChange,
}: {
  conversion: Conversion;
  disabled: boolean;
  onChange(type: string): Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState(conversion.documentType ?? "unknown");
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const current = conversion.documentType ?? "unknown";
  const canUseStatement =
    current === "bank_statement" ||
    (current === "unknown" &&
      conversion.transactions?.length > 0 &&
      !conversion.financialDocument) ||
    conversion.documentTypeHistory?.some((item) =>
      ["bank_statement", "unknown"].includes(item.before),
    );
  if (
    !conversion.pdf ||
    ["FAILED", "PROCESSING"].includes(conversion.status) ||
    !Object.hasOwn(labels, current)
  )
    return null;
  return (
    <section className="document-type-control" aria-label="Tipo do documento">
      <span>
        Tipo: <strong>{labels[current]}</strong>
      </span>{" "}
      <button
        className="secondary-button small"
        disabled={disabled || saving}
        onClick={() => setOpen(!open)}
      >
        Alterar tipo
      </button>
      {open && (
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setSaving(true);
            setError(undefined);
            try {
              await onChange(kind);
              setOpen(false);
            } catch (failure) {
              setError(
                failure instanceof Error
                  ? failure.message
                  : "Não foi possível alterar o tipo.",
              );
            } finally {
              setSaving(false);
            }
          }}
        >
          <label>
            Classificar como{" "}
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              disabled={saving}
            >
              <option value="unknown" disabled>
                Selecione o tipo
              </option>
              <option value="bank_statement" disabled={!canUseStatement}>
                Extrato
              </option>
              <option value="bank_receipt">Comprovante</option>
              <option value="fiscal_invoice">Nota fiscal</option>
            </select>
          </label>
          {!canUseStatement && (
            <p>A opção Extrato exige uma leitura bancária anterior.</p>
          )}
          <p>
            {kind === "bank_statement"
              ? "Restaura a leitura de extrato anterior, quando disponível, incluindo seus valores nos totais."
              : "Os lançamentos deste arquivo deixam de compor os totais bancários. O original e a leitura anterior ficam preservados."}
          </p>
          <p>
            A alteração registra o histórico e exige nova conferência. Ela não
            relê o arquivo nem confirma pagamento.
          </p>
          {error && <p role="alert">{error}</p>}
          <button
            className="primary-button"
            disabled={
              disabled || saving || kind === current || kind === "unknown"
            }
          >
            {saving ? "Salvando…" : "Confirmar alteração"}
          </button>{" "}
          <button
            type="button"
            className="secondary-button"
            disabled={saving}
            onClick={() => setOpen(false)}
          >
            Cancelar
          </button>
        </form>
      )}
      {!!conversion.documentTypeHistory?.length && (
        <details>
          <summary>Histórico do tipo</summary>
          <ul>
            {conversion.documentTypeHistory.map((item, index) => (
              <li key={index}>
                {labels[item.before] ?? item.before} →{" "}
                {labels[item.after] ?? item.after} ·{" "}
                {new Date(item.changed_at).toLocaleString("pt-BR")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
