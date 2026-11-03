"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  FileText,
} from "lucide-react";
import {
  decimalCents,
  financialMoney,
  loadFinancialMonth,
  type FinancialMonth,
} from "@/data/financial-center-service";
import { dateLabel, monthLabel, shiftMonth } from "@/domain/financial-center";
import styles from "./financial-center.module.css";

const tabs = [
  "Calendário",
  "Lançamentos",
  "Conciliação",
  "Fechamento",
  "Relatórios",
] as const;
type Tab = (typeof tabs)[number];

export function LiveFinancialCenter({
  clientId,
  initialQuery = "",
}: {
  clientId: string;
  initialQuery?: string;
}) {
  const initial = new URLSearchParams(initialQuery);
  const [month, setMonth] = useState(() =>
    /^20\d{2}-(0[1-9]|1[0-2])$/.test(initial.get("month") ?? "")
      ? initial.get("month")!
      : new Date()
          .toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" })
          .slice(0, 7),
  );
  const [account, setAccount] = useState(initial.get("accounts") ?? "");
  const [day, setDay] = useState(initial.get("day") ?? "");
  const [pending, setPending] = useState(initial.get("pending") === "true");
  const [tab, setTab] = useState<Tab>(
    tabs.find((value) => value === initial.get("tab")) ?? "Calendário",
  );
  const [cursor, setCursor] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [snapshot, setSnapshot] = useState<{
    key: string;
    data: FinancialMonth;
  } | null>(null);
  const [failure, setFailure] = useState<{
    key: string;
    message: string;
  } | null>(null);
  const query = new URLSearchParams({ month, limit: "50" });
  if (account) query.set("account", account);
  if (day.startsWith(month + "-"))
    query.set("day", String(Number(day.slice(-2))));
  if (pending) query.set("pending", "true");
  if (cursor) query.set("cursor", cursor);
  const request = query.toString();
  const key = `${clientId}:${request}:${attempt}`;
  const data = snapshot?.key === key ? snapshot.data : null;
  const error = failure?.key === key ? failure.message : null;
  const context = new URLSearchParams({ month, accounts: account, tab });
  if (day) context.set("day", day);
  if (pending) context.set("pending", "true");
  const returnTo = `/financial/${encodeURIComponent(clientId)}?${context}`;

  useEffect(() => {
    const controller = new AbortController();
    void loadFinancialMonth(clientId, request, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setSnapshot({ key, data: result });
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setSnapshot(null);
          setFailure({
            key,
            message:
              caught instanceof Error
                ? caught.message
                : "Consulta indisponível.",
          });
        }
      });
    return () => controller.abort();
  }, [clientId, request, key]);
  useEffect(() => {
    window.history.replaceState(null, "", returnTo);
  }, [returnTo]);
  useEffect(() => {
    const refresh = () => {
      setSnapshot(null);
      setAttempt((value) => value + 1);
    };
    const focus = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    const timer = data?.access_expires_at
      ? window.setTimeout(
          refresh,
          Math.max(
            0,
            Math.min(
              2147483647,
              Date.parse(data.access_expires_at) - Date.now(),
            ),
          ),
        )
      : undefined;
    return () => {
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
      window.clearTimeout(timer);
    };
  }, [data?.access_expires_at]);

  function changeMonth(delta: number) {
    setMonth(shiftMonth(month, delta));
    setDay("");
    setCursor("");
  }
  const observed = data?.days.filter((item) => item.count > 0) ?? [];
  const currencies = new Set(observed.map((item) => item.currency));
  const canSum =
    observed.length > 0 &&
    currencies.size === 1 &&
    !currencies.has(null) &&
    observed.every((item) => item.consolidation === "observed");
  const currency = observed[0]?.currency ?? "BRL";
  const sum = (field: "inflows" | "outflows") =>
    canSum
      ? financialMoney(
          observed.reduce(
            (total, item) => total + decimalCents(item[field]!),
            0n,
          ),
          currency,
        )
      : "Não consolidado";
  const pendingCount =
    data?.days.reduce((total, item) => total + item.pending_count, 0) ?? 0;
  const count = data?.days.reduce((total, item) => total + item.count, 0) ?? 0;
  const metadata = snapshot?.data.client.id === clientId ? snapshot.data : null;

  const table = data && (
    <div className={styles.dayDetail}>
      <div className={styles.panelHeading}>
        <h3>{day ? `Movimentos de ${dateLabel(day)}` : "Movimentos do mês"}</h3>
        {day && (
          <button
            className="secondary-button small"
            onClick={() => {
              setDay("");
              setCursor("");
            }}
          >
            Ver mês inteiro
          </button>
        )}
      </div>
      <label>
        <input
          type="checkbox"
          checked={pending}
          onChange={(event) => {
            setPending(event.target.checked);
            setCursor("");
          }}
        />
        Somente pendências de revisão
      </label>
      {!data.movements.length ? (
        <p role="status">
          Nenhum movimento acessível neste recorte. A cobertura pode ser
          desconhecida.
        </p>
      ) : (
        <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Data</th>
                <th>Descrição</th>
                <th>Banco / conta</th>
                <th>Valor</th>
                <th>Revisão</th>
                <th>Origem</th>
              </tr>
            </thead>
            <tbody>
              {data.movements.map((row) => (
                <tr key={row.id}>
                  <td>{dateLabel(row.date)}</td>
                  <td>
                    {row.description}
                    {row.possible_duplicate && (
                      <small className={styles.warning}>
                        Possível duplicidade entre extratos · conferir
                      </small>
                    )}
                  </td>
                  <td>
                    {data.accounts.find((item) => item.id === row.account_id)
                      ?.label ?? "Conta a confirmar"}
                  </td>
                  <td
                    className={
                      row.amount.startsWith("-") ? styles.debit : styles.credit
                    }
                  >
                    {financialMoney(decimalCents(row.amount), row.currency)}
                  </td>
                  <td>
                    {row.review_state === "reviewed"
                      ? "Movimento revisado"
                      : "Revisão pendente"}
                  </td>
                  <td>
                    <Link
                      className={styles.sourceLink}
                      href={`/review/${encodeURIComponent(row.source.batch_id)}/${encodeURIComponent(row.source.job_id)}?return_to=${encodeURIComponent(returnTo)}`}
                    >
                      Abrir origem · {row.source.filename}
                      {row.source.page ? ` · p. ${row.source.page}` : ""}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {cursor && (
        <button className="secondary-button" onClick={() => setCursor("")}>
          Primeira página
        </button>
      )}
      {data.next_cursor && (
        <button
          className="secondary-button"
          onClick={() => setCursor(data.next_cursor!)}
        >
          Próxima página
        </button>
      )}
    </div>
  );

  return (
    <main className={styles.page}>
      <div className={styles.demoNotice}>
        <strong>Consulta de movimentos bancários</strong>
        <span>Somente leitura. Correções continuam no conversor PRO.</span>
      </div>
      <div className={styles.breadcrumb}>
        <Link href="/dashboard">Clientes</Link>
        <ChevronRight size={14} />
        <span>{metadata?.client.name ?? "Cliente selecionado"}</span>
      </div>
      <header className={styles.heading}>
        <div>
          <span className={styles.eyebrow}>CENTRAL FINANCEIRA</span>
          <h1>{metadata?.client.name ?? "Central Financeira"}</h1>
          <p>{metadata?.client.tax_id}</p>
        </div>
        <button
          className="secondary-button"
          onClick={() => {
            setCursor("");
            setSnapshot(null);
            setAttempt((value) => value + 1);
          }}
        >
          Atualizar
        </button>
      </header>
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
        <span className={styles.status}>Consulta · cobertura a confirmar</span>
        <label>
          Conta{" "}
          <select
            aria-label="Conta"
            value={account}
            onChange={(event) => {
              setAccount(event.target.value);
              setCursor("");
            }}
          >
            <option value="">Todas as contas</option>
            {metadata?.accounts.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </section>
      {error ? (
        <div role="alert" className={styles.callout}>
          <p>{error}</p>
          <button
            className="secondary-button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Tentar novamente
          </button>
        </div>
      ) : !data ? (
        <p role="status">Carregando movimentos…</p>
      ) : (
        <>
          {(data.legacy_sources > 0 || data.unavailable_sources > 0) && (
            <p className={styles.warning} role="status">
              Consulta parcial: {data.legacy_sources} extrato(s) ainda usam o
              conversor anterior e {data.unavailable_sources} origem(ns) estão
              suspensas para consulta. Não estão incluídos nos valores abaixo.
            </p>
          )}
          <section className={styles.metrics} aria-label="Resumo bancário">
            <div className={styles.metric}>
              <span>Movimentações</span>
              <strong>{count}</strong>
              <small>Registros canônicos acessíveis</small>
            </div>
            <div className={styles.metric}>
              <span>Entradas bancárias</span>
              <strong>{sum("inflows")}</strong>
              <small>Créditos observados, incluindo transferências</small>
            </div>
            <div className={styles.metric}>
              <span>Saídas bancárias</span>
              <strong>{sum("outflows")}</strong>
              <small>Débitos observados</small>
            </div>
            <button
              className={styles.pendingMetric}
              onClick={() => {
                setTab("Lançamentos");
                setDay("");
                setPending(true);
                setCursor("");
              }}
            >
              <span>
                <CircleAlert size={18} />
                Precisam de atenção
              </span>
              <strong>{pendingCount}</strong>
              <small>Consultar pendências →</small>
            </button>
          </section>
          <div className={styles.coverage}>
            <FileText size={18} />
            <p>
              <strong>Cobertura desconhecida ou parcial.</strong> Dias sem
              registros não comprovam ausência de movimentação. Revisão de
              extrato não equivale a conciliação financeira.
            </p>
          </div>
          {!canSum && count > 0 && (
            <p className={styles.warning}>
              Totais não consolidados: confira possíveis duplicidades ou moedas
              distintas nas origens. Nenhum movimento foi mesclado ou descartado
              automaticamente.
            </p>
          )}
          <nav className={styles.tabs} aria-label="Áreas da Central Financeira">
            {tabs.map((item) => (
              <button
                key={item}
                aria-pressed={tab === item}
                onClick={() => {
                  setTab(item);
                  setCursor("");
                }}
              >
                {item}
              </button>
            ))}
          </nav>
          {tab === "Calendário" ? (
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
                    Selecione um dia para consultar os movimentos e suas
                    origens.
                  </p>
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
                      <div key={index} className={styles.blank} />
                    ),
                  )}
                  {data.days.map((item) => (
                    <button
                      key={item.date}
                      className={`${styles.day} ${item.coverage === "unknown" ? styles.uncovered : ""} ${item.pending_count ? styles.pendingDay : ""}`}
                      aria-pressed={day === item.date}
                      aria-label={`${dateLabel(item.date)}: ${item.count} movimentações${item.pending_count ? ", com pendências" : ""}`}
                      onClick={() => {
                        setDay(item.date);
                        setCursor("");
                      }}
                    >
                      <span className={styles.dayNumber}>
                        {Number(item.date.slice(-2))}
                        {item.pending_count > 0 && <CircleAlert size={14} />}
                      </span>
                      {item.count > 0 ? (
                        <>
                          {item.currency &&
                          item.inflows !== null &&
                          item.outflows !== null ? (
                            <>
                              <span className={styles.credit}>
                                +{" "}
                                {financialMoney(
                                  decimalCents(item.inflows),
                                  item.currency,
                                )}
                              </span>
                              <span className={styles.debit}>
                                −{" "}
                                {financialMoney(
                                  decimalCents(item.outflows),
                                  item.currency,
                                )}
                              </span>
                            </>
                          ) : (
                            <small>Totais para revisão</small>
                          )}
                          <small>{item.count} movimentações</small>
                        </>
                      ) : (
                        <small>
                          {item.coverage === "no_activity_confirmed"
                            ? "Sem movimentações"
                            : "Sem extratos conhecidos"}
                        </small>
                      )}
                      {item.coverage === "unknown" && (
                        <small>Cobertura desconhecida</small>
                      )}
                    </button>
                  ))}
                </div>
              </div>
              {day && table}
            </section>
          ) : tab === "Lançamentos" ? (
            <section className={styles.panel}>{table}</section>
          ) : (
            <section className={styles.panel}>
              <h2>{tab} · em planejamento</h2>
              <p>
                Esta área ainda não está operacional. A consulta atual não
                concilia nem fecha o financeiro e não exporta relatórios da
                Central.
              </p>
            </section>
          )}
        </>
      )}
    </main>
  );
}
