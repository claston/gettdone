"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Download,
  FileText,
  ListChecks,
} from "lucide-react";
import {
  financialDemoRepository,
  financialScenarios,
} from "@/data/financial-demo";
import {
  assessClosing,
  calendarDays,
  dateLabel,
  filterMovements,
  money,
  monthLabel,
  projectMovements,
  projectDocumentReviews,
  scopedDocumentReviews,
  reportCsv,
  scopedFindings,
  shiftMonth,
  totals,
  type FinancialMovement,
  type FinancialDocumentReview,
  type FinancialScenario,
  type MovementFilters,
} from "@/domain/financial-center";
import styles from "./financial-center.module.css";

const tabs = [
  "Calendário",
  "Lançamentos",
  "Conciliação",
  "Fechamento",
  "Relatórios",
] as const;
type Tab = (typeof tabs)[number];
const findingLabels = {
  review: "Revisão",
  suggestion: "Correspondências sugeridas",
  unmatched: "Sem correspondência",
  duplicate: "Possíveis duplicidades",
  divergence: "Divergências",
};

export function FinancialCenter({
  scenario,
  initialQuery = "",
}: {
  scenario: FinancialScenario;
  initialQuery?: string;
}) {
  const router = useRouter();
  const query = new URLSearchParams(initialQuery);
  const initialMonth = query.get("month");
  const initialAccounts = query.get("accounts");
  const [month, setMonth] = useState(
    initialMonth && /^20\d{2}-(0[1-9]|1[0-2])$/.test(initialMonth)
      ? initialMonth
      : scenario.month,
  );
  const [accounts, setAccounts] = useState<string[]>(
    initialAccounts === null
      ? scenario.accounts.map((account) => account.id)
      : initialAccounts
          .split(",")
          .filter((id) =>
            scenario.accounts.some((account) => account.id === id),
          ),
  );
  const [tab, setTab] = useState<Tab>(
    tabs.find((item) => item === query.get("tab")) ?? "Calendário",
  );
  const [day, setDay] = useState<string | null>(query.get("day"));
  const [snapshot, setSnapshot] = useState<{
    rows: FinancialMovement[];
    documents: FinancialDocumentReview[];
  } | null>(null);
  const allRows = snapshot?.rows ?? null;
  const documentReviews = snapshot?.documents ?? [];
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [filters, setFilters] = useState({
    from: "",
    to: "",
    description: "",
    direction: "all",
    review: "all",
  } as Pick<
    MovementFilters,
    "from" | "to" | "description" | "direction" | "review"
  >);
  const [focusIds, setFocusIds] = useState<readonly string[] | null>(null);
  const [closing, setClosing] = useState<{
    scope: string;
    text: string;
  } | null>(null);
  const scope = `${month}:${[...accounts].sort().join(",")}`;
  const closingText = closing?.scope === scope ? closing.text : "Não concluído";

  useEffect(() => {
    let active = true;
    financialDemoRepository(scenario)
      .getBatch(scenario.batch.id)
      .then((batch) => {
        if (active) {
          setSnapshot({
            rows: projectMovements(scenario, batch),
            documents: projectDocumentReviews(scenario, batch),
          });
          setError(false);
        }
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, [scenario, attempt]);

  const contextQuery = new URLSearchParams({
    month,
    accounts: accounts.join(","),
    tab,
  });
  if (day) contextQuery.set("day", day);
  const returnTo = `/demo/financial/${scenario.id}?${contextQuery}`;
  useEffect(() => {
    window.history.replaceState(null, "", returnTo);
  }, [returnTo]);

  const rows = filterMovements(allRows ?? [], { month, accountIds: accounts });
  const sum = totals(rows);
  const findings = scopedFindings(scenario, rows);
  const pendingDocuments = scopedDocumentReviews(
    documentReviews,
    allRows ?? [],
    month,
    accounts,
  ).filter((document) => !document.reviewed);
  const days = calendarDays(scenario, allRows ?? [], month, accounts);
  const coveredDays = days.filter((item) => item.covered).length;
  const visibleRows = filterMovements(rows, {
    month,
    accountIds: accounts,
    ...filters,
  }).filter((row) => !focusIds || focusIds.includes(row.id));
  const pendingCount =
    new Set([
      ...rows.filter((row) => row.pending).map((row) => row.id),
      ...findings
        .flatMap((finding) => finding.movementIds)
        .filter((id) => rows.some((row) => row.id === id)),
    ]).size +
    pendingDocuments.filter(
      (document) =>
        !rows.some(
          (row) => row.source.conversionId === document.source.conversionId,
        ),
    ).length;
  function changeMonth(delta: number) {
    setMonth(shiftMonth(month, delta));
    setDay(null);
    setFilters({
      from: "",
      to: "",
      description: "",
      direction: "all",
      review: "all",
    });
    setFocusIds(null);
    setClosing(null);
  }
  function investigate(ids: readonly string[]) {
    setFocusIds(ids);
    setFilters({
      from: "",
      to: "",
      description: "",
      direction: "all",
      review: "all",
    });
    setTab("Lançamentos");
  }

  return (
    <main className={styles.page}>
      <div className={styles.demoNotice}>
        <strong>Protótipo · dados fictícios</strong>
        <span>
          Alterações e simulações duram apenas nesta navegação. Recarregar
          reinicia a demonstração.
        </span>
      </div>
      <div className={styles.breadcrumb}>
        <Link href="/demo/financial/clients">Clientes</Link>
        <ChevronRight size={14} />
        <span>{scenario.client.name}</span>
      </div>
      <header className={styles.heading}>
        <div>
          <span className={styles.eyebrow}>CENTRAL FINANCEIRA</span>
          <h1>{scenario.client.name}</h1>
          <p>{scenario.client.taxId}</p>
        </div>
        <label className={styles.scenario}>
          Cenário de demonstração
          <select
            aria-label="Cenário de demonstração"
            value={scenario.id}
            onChange={(event) => {
              router.push(`/demo/financial/${event.target.value}`);
            }}
          >
            {financialScenarios.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </header>
      {scenario.trial && (
        <div className={styles.callout}>
          <strong>Seu primeiro extrato, organizado no mês</strong>
          <p>
            Cliente e conta identificados no documento fictício. Esta simulação
            da degustação de 24 horas mostra somente um extrato, com cobertura
            de 1 a 15 de setembro. Não é uma concessão real de acesso.
          </p>
          <Link
            href={`/demo/financial/${scenario.id}/review/${scenario.batch.conversions[0].id}?returnTo=${encodeURIComponent(returnTo)}`}
          >
            Abrir a primeira conversão no revisor →
          </Link>
        </div>
      )}
      <section className={styles.context} aria-label="Contexto financeiro">
        <div className={styles.month}>
          <button
            className="secondary-button"
            aria-label="Mês anterior"
            onClick={() => changeMonth(-1)}
          >
            <ChevronLeft size={17} />
          </button>
          <h2>{monthLabel(month)}</h2>
          <button
            className="secondary-button"
            aria-label="Próximo mês"
            onClick={() => changeMonth(1)}
          >
            <ChevronRight size={17} />
          </button>
        </div>
        <span className={styles.status}>
          {closingText !== "Não concluído"
            ? "Fechamento simulado"
            : pendingCount
              ? "Mês com pendências"
              : coveredDays === days.length
                ? "Disponível para conferência"
                : "Cobertura incompleta"}
        </span>
        <fieldset className={styles.accounts}>
          <legend>Contas consideradas</legend>
          {scenario.accounts.map((account) => (
            <label key={account.id}>
              <input
                type="checkbox"
                checked={accounts.includes(account.id)}
                onChange={() => {
                  setAccounts((current) =>
                    current.includes(account.id)
                      ? current.filter((id) => id !== account.id)
                      : [...current, account.id],
                  );
                  setClosing(null);
                  setFocusIds(null);
                }}
              />
              {account.label}
            </label>
          ))}
        </fieldset>
      </section>
      {error ? (
        <div className={styles.callout} role="alert">
          <p>Não foi possível carregar a demonstração.</p>
          <button
            className="secondary-button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Tentar novamente
          </button>
        </div>
      ) : !allRows ? (
        <p role="status">Carregando movimentações…</p>
      ) : (
        <>
          <section className={styles.metrics} aria-label="Indicadores do mês">
            <Metric
              label="Movimentações"
              value={String(sum.count)}
              icon={<ListChecks size={19} />}
              detail="No recorte selecionado"
            />
            <Metric
              label="Entradas"
              value={money(sum.credits)}
              icon={<ArrowDownLeft size={19} />}
              detail="Créditos nos extratos"
            />
            <Metric
              label="Saídas"
              value={money(sum.debits)}
              icon={<ArrowUpRight size={19} />}
              detail="Débitos nos extratos"
            />
            <button
              className={styles.pendingMetric}
              onClick={() =>
                setTab(pendingDocuments.length ? "Fechamento" : "Conciliação")
              }
            >
              <span>
                <CircleAlert size={19} />
                Precisam de atenção
              </span>
              <strong>{pendingCount}</strong>
              <small>Investigar pendências →</small>
            </button>
          </section>
          <div className={styles.coverage}>
            <FileText size={18} />
            <p>
              <strong>
                {coveredDays === days.length
                  ? "Cobertura documental do mês"
                  : "Cobertura parcial ou ausente"}
              </strong>{" "}
              · {coveredDays} de {days.length} dias cobertos em todas as contas
              selecionadas.{" "}
              {coveredDays < days.length &&
                "Dias sem documento não significam dias sem movimentação."}{" "}
              Totais provisórios para conferência.
            </p>
          </div>
          {!accounts.length && (
            <p className={styles.callout} role="status">
              Selecione uma conta para visualizar as movimentações.
            </p>
          )}
          <nav className={styles.tabs} aria-label="Áreas da Central Financeira">
            {tabs.map((item) => (
              <button
                key={item}
                aria-pressed={tab === item}
                onClick={() => setTab(item)}
              >
                {item}
                {item === "Conciliação" && findings.length > 0 && (
                  <span>{findings.length}</span>
                )}
              </button>
            ))}
          </nav>
          {tab === "Calendário" && (
            <section
              className={styles.panel}
              aria-label="Calendário financeiro"
            >
              <div className={styles.panelHeading}>
                <div>
                  <h2>
                    <CalendarDays size={19} />
                    Seu mês, dia a dia
                  </h2>
                  <p>
                    Selecione um dia para consultar os lançamentos e suas
                    origens.
                  </p>
                </div>
                <div className={styles.legend}>
                  <span>● Movimentações</span>
                  <span>● Pendências</span>
                  <span>▨ Sem cobertura</span>
                </div>
              </div>
              <div className={styles.calendarScroll}>
                <div className={styles.calendar}>
                  {["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"].map(
                    (label) => (
                      <div className={styles.weekday} key={label}>
                        {label}
                      </div>
                    ),
                  )}
                  {Array.from(
                    { length: new Date(`${month}-01T12:00:00Z`).getUTCDay() },
                    (_, index) => (
                      <div key={`blank-${index}`} className={styles.blank} />
                    ),
                  )}
                  {days.map((item) => (
                    <button
                      key={item.date}
                      className={`${styles.day} ${!item.covered ? styles.uncovered : ""} ${item.pending ? styles.pendingDay : ""}`}
                      aria-pressed={day === item.date}
                      aria-label={`${dateLabel(item.date)}: ${item.count} movimentações${item.pending ? ", com pendências" : ""}${!item.covered ? ", sem cobertura suficiente" : ""}`}
                      onClick={() => setDay(item.date)}
                    >
                      <span className={styles.dayNumber}>
                        {Number(item.date.slice(-2))}
                        {item.pending && <CircleAlert size={14} />}
                      </span>
                      {item.count > 0 ? (
                        <>
                          <span className={styles.credit}>
                            + {money(item.credits)}
                          </span>
                          <span className={styles.debit}>
                            − {money(item.debits)}
                          </span>
                          <small>
                            {item.count}{" "}
                            {item.count === 1
                              ? "movimentação"
                              : "movimentações"}
                          </small>
                        </>
                      ) : (
                        <small>
                          {item.covered ? "Sem movimentações" : "Sem cobertura"}
                        </small>
                      )}
                      {!item.covered && item.count > 0 && (
                        <small>Sem cobertura suficiente</small>
                      )}
                    </button>
                  ))}
                </div>
              </div>
              {day && (
                <div className={styles.dayDetail}>
                  <div className={styles.panelHeading}>
                    <h3>Lançamentos de {dateLabel(day)}</h3>
                    <button
                      className="secondary-button small"
                      onClick={() => setDay(null)}
                    >
                      Fechar detalhe
                    </button>
                  </div>
                  {!days.find((item) => item.date === day)?.covered && (
                    <p className={styles.warning}>
                      A cobertura deste dia é insuficiente. A lista pode estar
                      incompleta.
                    </p>
                  )}
                  <MovementTable
                    rows={rows.filter((row) => row.date === day)}
                    scenario={scenario}
                    returnTo={returnTo}
                  />
                  {rows.some((row) => row.date === day && row.pending) && (
                    <button
                      className="secondary-button"
                      onClick={() =>
                        investigate(
                          rows
                            .filter((row) => row.date === day && row.pending)
                            .map((row) => row.id),
                        )
                      }
                    >
                      Ver somente lançamentos para revisão
                    </button>
                  )}
                </div>
              )}
            </section>
          )}
          {tab === "Lançamentos" && (
            <section className={styles.panel}>
              <div className={styles.panelHeading}>
                <div>
                  <h2>Lançamentos financeiros</h2>
                  <p>
                    Uma lista por cliente, com a origem de cada movimentação.
                  </p>
                </div>
                <strong>{visibleRows.length} lançamentos</strong>
              </div>
              <div className={styles.filters}>
                <label>
                  De
                  <input
                    type="date"
                    aria-label="Data inicial"
                    value={filters.from}
                    onChange={(event) =>
                      setFilters({ ...filters, from: event.target.value })
                    }
                  />
                </label>
                <label>
                  Até
                  <input
                    type="date"
                    aria-label="Data final"
                    value={filters.to}
                    onChange={(event) =>
                      setFilters({ ...filters, to: event.target.value })
                    }
                  />
                </label>
                <label>
                  Descrição
                  <input
                    type="search"
                    placeholder="Buscar descrição"
                    value={filters.description}
                    onChange={(event) =>
                      setFilters({
                        ...filters,
                        description: event.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  Tipo
                  <select
                    value={filters.direction}
                    onChange={(event) =>
                      setFilters({
                        ...filters,
                        direction: event.target
                          .value as MovementFilters["direction"],
                      })
                    }
                  >
                    <option value="all">Todas as movimentações</option>
                    <option value="credit">Entradas</option>
                    <option value="debit">Saídas</option>
                  </select>
                </label>
                <label>
                  Revisão
                  <select
                    value={filters.review}
                    onChange={(event) =>
                      setFilters({
                        ...filters,
                        review: event.target.value as MovementFilters["review"],
                      })
                    }
                  >
                    <option value="all">Todas as situações</option>
                    <option value="pending">Revisão necessária</option>
                    <option value="reviewed">Conferidos</option>
                  </select>
                </label>
                <button
                  className="secondary-button"
                  onClick={() => {
                    setFilters({
                      from: "",
                      to: "",
                      description: "",
                      direction: "all",
                      review: "all",
                    });
                    setFocusIds(null);
                  }}
                >
                  Limpar filtros
                </button>
              </div>
              {focusIds && (
                <p className={styles.warning}>
                  Exibindo lançamentos da pendência selecionada. Use “Limpar
                  filtros” para ver os demais.
                </p>
              )}
              <MovementTable
                rows={visibleRows}
                scenario={scenario}
                returnTo={returnTo}
              />
            </section>
          )}
          {tab === "Conciliação" && (
            <section className={styles.panel}>
              <div className={styles.panelHeading}>
                <div>
                  <h2>O que precisa de atenção</h2>
                  <p>
                    Sugestões demonstrativas. Nenhum movimento foi conciliado
                    automaticamente.
                  </p>
                </div>
              </div>
              {findings.length === 0 ? (
                <p className={styles.empty}>
                  Nenhuma pendência simulada neste recorte. Isso não comprova
                  conciliação nem cobertura completa.
                </p>
              ) : (
                <div className={styles.findings}>
                  {findings.map((finding) => (
                    <article key={finding.id} className={styles.finding}>
                      <span
                        className={
                          finding.blocking ? styles.warning : styles.tag
                        }
                      >
                        {findingLabels[finding.kind]}
                        {finding.blocking ? " · bloqueia fechamento" : ""}
                      </span>
                      <h3>{finding.title}</h3>
                      <p>{finding.detail}</p>
                      <p>
                        {
                          finding.movementIds.filter((id) =>
                            rows.some((row) => row.id === id),
                          ).length
                        }{" "}
                        de {finding.movementIds.length} movimentos no recorte
                        {finding.movementIds.some(
                          (id) => !rows.some((row) => row.id === id),
                        )
                          ? " · contraparte fora das contas selecionadas"
                          : ""}
                      </p>
                      <button
                        className="secondary-button"
                        onClick={() => investigate(finding.movementIds)}
                      >
                        Investigar lançamentos
                      </button>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}
          {tab === "Fechamento" && (
            <Closing
              key={scope}
              scenario={scenario}
              rows={allRows}
              documentReviews={documentReviews}
              returnTo={returnTo}
              month={month}
              accounts={accounts}
              completed={closingText}
              onComplete={(text) => setClosing({ scope, text })}
              onInvestigate={() => setTab("Conciliação")}
            />
          )}
          {tab === "Relatórios" && (
            <section className={styles.panel}>
              <div className={styles.panelHeading}>
                <div>
                  <h2>Relatórios do mês</h2>
                  <p>
                    {monthLabel(month)} · {accounts.length} contas ·{" "}
                    {rows.length} movimentações
                  </p>
                </div>
                <button
                  className="primary-button"
                  disabled={!accounts.length}
                  onClick={() => {
                    const blob = new Blob(
                      [
                        reportCsv(
                          scenario,
                          allRows,
                          month,
                          accounts,
                          closingText,
                        ),
                      ],
                      { type: "text/csv;charset=utf-8" },
                    );
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = `DEMONSTRACAO-${scenario.id}-${month}.csv`;
                    link.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }}
                >
                  <Download size={16} />
                  Baixar CSV demonstrativo
                </button>
              </div>
              <p className={styles.callout}>
                O CSV pode ser aberto no Excel e inclui movimentações, resumo
                por conta, entradas, saídas, pendências, origem e fechamento
                simulado. Não representa saldos nem receitas e despesas
                contábeis.
              </p>
              <div className={styles.tableScroll}>
                <table className={styles.table}>
                  <caption>Resumo por conta · totais provisórios</caption>
                  <thead>
                    <tr>
                      <th>Conta</th>
                      <th>Movimentações</th>
                      <th>Entradas</th>
                      <th>Saídas</th>
                      <th>Cobertura</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scenario.accounts
                      .filter((account) => accounts.includes(account.id))
                      .map((account) => {
                        const total = totals(
                          rows.filter((row) => row.accountId === account.id),
                        );
                        const covered = calendarDays(scenario, allRows, month, [
                          account.id,
                        ]).filter((item) => item.covered).length;
                        return (
                          <tr key={account.id}>
                            <td>{account.label}</td>
                            <td>{total.count}</td>
                            <td>{money(total.credits)}</td>
                            <td>{money(total.debits)}</td>
                            <td>
                              {covered}/{days.length} dias
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>
              <h3>Informações de fechamento</h3>
              <p>{closingText}</p>
              <h3>Pendências do recorte</h3>
              <p>
                {findings.length
                  ? findings.map((finding) => finding.title).join(" · ")
                  : "Nenhuma pendência simulada."}
              </p>
              <button className="secondary-button" disabled>
                Excel (.xlsx) · em preparação
              </button>
              <p className={styles.muted}>
                A exportação Excel consolidada dependerá da leitura canônica
                autorizada. O conversor mantém suas exportações atuais.
              </p>
            </section>
          )}
          {scenario.sources.some(
            (source) => source.availability === "expired",
          ) && (
            <section className={styles.expired}>
              <h3>Documentos expirados</h3>
              {scenario.sources
                .filter((source) => source.availability === "expired")
                .map((source) => (
                  <p key={source.conversionId}>
                    {source.filename} · conteúdo financeiro e PDF indisponíveis.
                    Apenas referência histórica fictícia.
                  </p>
                ))}
            </section>
          )}
        </>
      )}
    </main>
  );
}

function Metric({
  label,
  value,
  detail,
  icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon: React.ReactNode;
}) {
  return (
    <article className={styles.metric}>
      <span>
        {icon}
        {label}
      </span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}
function MovementTable({
  rows,
  scenario,
  returnTo,
}: {
  rows: readonly FinancialMovement[];
  scenario: FinancialScenario;
  returnTo: string;
}) {
  if (!rows.length)
    return (
      <p className={styles.empty}>
        Nenhum lançamento disponível neste recorte. Consulte a cobertura
        documental antes de concluir que não houve movimentações.
      </p>
    );
  return (
    <div className={styles.tableScroll}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Data</th>
            <th>Descrição e origem</th>
            <th>Conta</th>
            <th>Valor</th>
            <th>Revisão</th>
            <th>Evidência</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{dateLabel(row.date)}</td>
              <td>
                <strong>{row.description}</strong>
                <small>
                  {row.source.filename} ·{" "}
                  {row.source.page
                    ? `p. ${row.source.page}`
                    : "página não informada"}
                </small>
              </td>
              <td>
                {
                  scenario.accounts.find(
                    (account) => account.id === row.accountId,
                  )?.label
                }
              </td>
              <td
                className={row.amountCents >= 0 ? styles.credit : styles.debit}
              >
                {money(row.amountCents)}
              </td>
              <td>
                <span className={row.pending ? styles.warning : styles.tag}>
                  {row.pending ? "Revisão necessária" : "Conferido"}
                </span>
              </td>
              <td>
                <Link
                  className={styles.sourceLink}
                  href={`/demo/financial/${scenario.id}/review/${row.source.conversionId}?returnTo=${encodeURIComponent(returnTo)}`}
                >
                  Abrir no revisor
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Closing({
  scenario,
  rows,
  documentReviews,
  returnTo,
  month,
  accounts,
  completed,
  onComplete,
  onInvestigate,
}: {
  scenario: FinancialScenario;
  rows: readonly FinancialMovement[];
  documentReviews: readonly FinancialDocumentReview[];
  returnTo: string;
  month: string;
  accounts: string[];
  completed: string;
  onComplete(text: string): void;
  onInvestigate(): void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [reservation, setReservation] = useState("");
  const assessment = assessClosing(
    scenario,
    rows,
    month,
    accounts,
    confirmed,
    reservation,
    documentReviews,
  );
  return (
    <section className={styles.panel}>
      <div className={styles.panelHeading}>
        <div>
          <h2>Conferência para fechamento</h2>
          <p>
            Simule a preparação mensal. Nenhum fechamento real será gravado.
          </p>
        </div>
      </div>
      <ol className={styles.closingSteps}>
        <li>
          <h3>1. Confirme as contas</h3>
          <p>
            {accounts.length
              ? scenario.accounts
                  .filter((account) => accounts.includes(account.id))
                  .map((account) => account.label)
                  .join(" · ")
              : "Nenhuma conta selecionada"}
          </p>
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />{" "}
            Confirmo estas contas para o fechamento de {monthLabel(month)}.
          </label>
        </li>
        <li>
          <h3>2. Confira cobertura e pendências</h3>
          {scenario.accounts
            .filter((account) => accounts.includes(account.id))
            .map((account) => {
              const days = calendarDays(scenario, rows, month, [account.id]);
              return (
                <p key={account.id}>
                  {account.label}: {days.filter((day) => day.covered).length}/
                  {days.length} dias com cobertura documental.
                </p>
              );
            })}
          <p>
            Conflitos de conta, lançamentos sem revisão e duplicidades não
            resolvidas exigem correção. Ressalvas não dispensam esses bloqueios.
          </p>
          <button className="secondary-button" onClick={onInvestigate}>
            Investigar pendências
          </button>
          {assessment.pendingDocuments.length > 0 && (
            <ul>
              {assessment.pendingDocuments.map(({ source }) => (
                <li key={source.conversionId}>
                  {source.filename} · revisão documental pendente.{" "}
                  <Link
                    className={styles.sourceLink}
                    href={`/demo/financial/${scenario.id}/review/${source.conversionId}?returnTo=${encodeURIComponent(returnTo)}`}
                  >
                    Revisar documento
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </li>
        <li>
          <h3>3. Documente as ressalvas</h3>
          <label className={styles.reservation}>
            Justificativa para este mês e contas
            <textarea
              rows={3}
              value={reservation}
              onChange={(event) => setReservation(event.target.value)}
              placeholder="Ex.: conta Inter com extrato somente até 15/09; restante do mês ainda não recebido."
            />
          </label>
          <p>
            Obrigatória quando há cobertura parcial ou pendências operacionais.
            A simulação não altera valores ou remove movimentos.
          </p>
        </li>
      </ol>
      {completed !== "Não concluído" ? (
        <div className={styles.callout} role="status">
          <strong>{completed}</strong>
          <p>
            Resultado apenas demonstrativo, sem persistência ou certificação
            contábil.
          </p>
          <button
            className="secondary-button"
            onClick={() => {
              onComplete("Não concluído");
              setConfirmed(false);
            }}
          >
            Reabrir simulação
          </button>
        </div>
      ) : (
        <>
          <ul className={styles.blockers}>
            {assessment.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
          <button
            className="primary-button"
            disabled={!assessment.allowed}
            onClick={() =>
              onComplete(
                assessment.withReservations
                  ? `Concluído com ressalvas (simulação): ${reservation.trim()}`
                  : "Concluído (simulação)",
              )
            }
          >
            Simular fechamento
            {assessment.withReservations ? " com ressalvas" : ""}
          </button>
        </>
      )}
    </section>
  );
}
