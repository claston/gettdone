"use client";

import { useEffect, useRef, useState } from "react";
import {
  parseBrazilianCurrency,
  validateTransactionField,
  type TransactionDraft,
} from "@/domain/workspace";

export function TransactionDialog({
  initialDate,
  onAdd,
  onClose,
}: {
  initialDate: string;
  onAdd(draft: TransactionDraft): void;
  onClose(): void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [date, setDate] = useState(initialDate);
  const [description, setDescription] = useState("");
  const [credit, setCredit] = useState("");
  const [debit, setDebit] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const element = dialog.current!;
    if (element.showModal) element.showModal();
    else element.setAttribute("open", "");
    return () => {
      element.close?.();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="transaction-dialog"
      aria-labelledby="transaction-dialog-title"
      onCancel={onClose}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const creditValue = parseBrazilianCurrency(credit);
          const debitValue = parseBrazilianCurrency(debit);
          const validation =
            validateTransactionField("date", date) ||
            validateTransactionField("description", description) ||
            validateTransactionField("credit", credit) ||
            validateTransactionField("debit", debit);
          if (validation) {
            setError(validation);
            return;
          }
          if (
            (creditValue === null) === (debitValue === null) ||
            (creditValue !== null && creditValue <= 0) ||
            (debitValue !== null && debitValue <= 0)
          ) {
            setError(
              "Informe um valor positivo em apenas um dos campos: crédito ou débito.",
            );
            return;
          }
          onAdd({
            date,
            description: description.trim(),
            credit: creditValue,
            debit: debitValue,
          });
          onClose();
        }}
      >
        <h2 id="transaction-dialog-title">Adicionar lançamento</h2>
        <label>
          Data
          <input
            value={date}
            onChange={(event) => setDate(event.target.value)}
            placeholder="DD/MM/AAAA"
            required
          />
        </label>
        <label>
          Descrição
          <input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            required
          />
        </label>
        <div className="transaction-dialog-amounts">
          <label>
            Crédito
            <input
              inputMode="decimal"
              value={credit}
              onChange={(event) => setCredit(event.target.value)}
              placeholder="0,00"
            />
          </label>
          <label>
            Débito
            <input
              inputMode="decimal"
              value={debit}
              onChange={(event) => setDebit(event.target.value)}
              placeholder="0,00"
            />
          </label>
        </div>
        {error && <p role="alert">{error}</p>}
        <div className="transaction-dialog-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            Cancelar
          </button>
          <button type="submit" className="primary-button">
            Incluir lançamento
          </button>
        </div>
      </form>
    </dialog>
  );
}
