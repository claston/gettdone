import type { BalanceCheck, Conversion, Transaction } from "@/domain/workspace";
import { formatCurrency } from "@/domain/workspace";
import {
  fieldReviewWarnings,
  reviewWarningText,
} from "@/domain/review-warnings";

const money = (value: number) => `R$ ${formatCurrency(value)}`;
const difference = (value: number) =>
  `${value > 0 ? "+" : value < 0 ? "−" : ""}${money(Math.abs(value))}`;

function BalanceEquation({
  check,
  statement = false,
  showResult = true,
}: {
  check: BalanceCheck;
  statement?: boolean;
  showResult?: boolean;
}) {
  const backwards = check.order === "descending";
  return (
    <div className="balance-equation">
      <p>
        {statement ? "Saldo de abertura" : "Saldo de referência"}:{" "}
        <strong>{money(check.referenceBalance)}</strong>
        {!statement && check.referenceDate && (
          <span>
            {" "}
            ({check.referenceDate}
            {check.referencePage ? `, pág. ${check.referencePage}` : ""})
          </span>
        )}
      </p>
      <p>
        {statement
          ? "Entradas e saídas"
          : `${check.movementsCount} ${check.movementsCount === 1 ? "lançamento considerado" : "lançamentos considerados"}`}
        : {money(check.creditsTotal)} em créditos e {money(check.debitsTotal)}{" "}
        em débitos.
      </p>
      {backwards && (
        <p>
          O extrato está em ordem decrescente; a conferência desfaz os
          movimentos para chegar ao saldo anterior.
        </p>
      )}
      <p className="balance-calculation">
        {money(check.referenceBalance)} {backwards ? "−" : "+"}{" "}
        {money(check.creditsTotal)} {backwards ? "+" : "−"}{" "}
        {money(check.debitsTotal)} = {money(check.expectedBalance)}
      </p>
      {showResult && (
        <>
          <p>
            Saldo esperado: <strong>{money(check.expectedBalance)}</strong>.
            Saldo registrado: <strong>{money(check.reportedBalance)}</strong>.
          </p>
          <p>
            Diferença (registrado − esperado):{" "}
            <strong>{difference(check.difference)}</strong>.
          </p>
        </>
      )}
    </div>
  );
}

export function BalanceReviewNotice({
  conversion,
}: {
  conversion: Conversion;
}) {
  const rows = conversion.transactions.filter(
    (row) =>
      !row.isDeleted &&
      row.warningTypes?.includes("balance_consistency_failed"),
  );
  const warnings =
    conversion.reviewWarnings ??
    conversion.transactions.flatMap((row) => row.warningTypes ?? []);
  const summaryWarning = warnings.includes("printed_summary_balance_mismatch");
  const financialWarning = warnings.includes("financial_validation_failed");
  const periodWarning = warnings.includes("document_period_unavailable");
  const balanceWarning = Boolean(
    rows.length ||
    summaryWarning ||
    financialWarning ||
    conversion.statementBalanceCheck,
  );
  if (
    !rows.length &&
    !summaryWarning &&
    !financialWarning &&
    !periodWarning &&
    !conversion.statementBalanceCheck
  )
    return null;
  const guidance = (
    <p>
      Os avisos mostram os valores da conferência registrada. Confira os
      lançamentos e os saldos no PDF antes de alterar valores ou marcar como
      revisado.
    </p>
  );
  return (
    <div
      className="page-sync-notice balance-review-notice"
      role="status"
      aria-label="Conferência do documento"
    >
      {periodWarning && (
        <p>
          <strong>Período do documento não identificado.</strong> Os movimentos
          legíveis foram convertidos. Confira a cobertura no PDF original; as
          datas dos lançamentos não comprovam que o mês está completo.
        </p>
      )}
      {balanceWarning && <strong>Saldos precisam de conferência.</strong>}{" "}
      {rows.length > 0 && (
        <span>
          {rows.length}{" "}
          {rows.length === 1
            ? "lançamento com saldo divergente"
            : "lançamentos com saldo divergente"}
          . Abra o aviso na linha para ver a comparação.
        </span>
      )}
      {(summaryWarning || conversion.statementBalanceCheck) && (
        <p>
          <strong>
            Saldo final não confere com o saldo de abertura e os lançamentos.
          </strong>
        </p>
      )}
      {financialWarning && (
        <p>
          Os controles financeiros do extrato não fecharam com os lançamentos
          convertidos.
        </p>
      )}
      {conversion.statementBalanceCheck && (
        <>
          <p>
            Saldo esperado:{" "}
            <strong>
              {money(conversion.statementBalanceCheck.expectedBalance)}
            </strong>
            . Saldo registrado:{" "}
            <strong>
              {money(conversion.statementBalanceCheck.reportedBalance)}
            </strong>
            .
          </p>
          <p>
            Diferença (registrado − esperado):{" "}
            <strong>
              {difference(conversion.statementBalanceCheck.difference)}
            </strong>
            .
          </p>
          <details>
            <summary>Ver cálculo do saldo final</summary>
            <BalanceEquation
              check={conversion.statementBalanceCheck}
              statement
              showResult={false}
            />
            {guidance}
          </details>
        </>
      )}
      {balanceWarning && !conversion.statementBalanceCheck && guidance}
    </div>
  );
}

export function TransactionReviewNote({
  transaction,
  onShowReference,
}: {
  transaction: Transaction;
  onShowReference?(): void;
}) {
  const warnings = fieldReviewWarnings(transaction.warningTypes ?? []);
  if (!warnings.length) return null;
  const id = `review-${transaction.id}`;
  if (
    !transaction.balanceCheck ||
    !warnings.includes("balance_consistency_failed")
  )
    return (
      <p id={id} className="transaction-review-note">
        {reviewWarningText(warnings)}
      </p>
    );
  return (
    <details id={id} className="transaction-review-note">
      <summary>
        Saldo não confere · diferença{" "}
        {difference(transaction.balanceCheck.difference)}
      </summary>
      <BalanceEquation check={transaction.balanceCheck} />
      {onShowReference && (
        <button type="button" onClick={onShowReference}>
          Ver lançamento de referência
        </button>
      )}
      <p>Confira o lançamento e os saldos de referência no PDF.</p>
      {warnings.some((code) => code !== "balance_consistency_failed") && (
        <p>{reviewWarningText([])}</p>
      )}
    </details>
  );
}
