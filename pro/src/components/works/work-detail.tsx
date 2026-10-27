"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import type {
  OrganizationService,
  OrganizedConversion,
} from "@/data/organization-service";
import type {
  StatementWork,
  StatementWorkService,
  WorkDocument,
  WorkEvidence,
} from "@/data/statement-work-service";
import styles from "../organization/organization.module.css";

type Run = (action: () => Promise<StatementWork>) => Promise<void>;
const tabs = [
  "Pendências",
  "Contas e cobertura",
  "Extratos e movimentações",
  "Resumo e exportação",
];
const coverageLabel: Record<string, string> = {
  complete: "Completa",
  partial: "Parcial",
  missing: "Ausente",
  overlapping: "Sobreposta",
  indeterminate: "Indeterminada",
};
const formatDate = (value: string) =>
  value.slice(0, 10).split("-").reverse().join("/");

function ReasonForm({
  label,
  button,
  onSubmit,
}: {
  label: string;
  button: string;
  onSubmit(reason: string): void;
}) {
  const [reason, setReason] = useState("");
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (reason.trim().length >= 3) onSubmit(reason);
      }}
    >
      <label>
        {label}
        <textarea
          required
          minLength={3}
          maxLength={2000}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      <button className="secondary-button" disabled={reason.trim().length < 3}>
        {button}
      </button>
    </form>
  );
}

function DocumentEditor({
  work,
  doc,
  service,
  run,
}: {
  work: StatementWork;
  doc: WorkDocument;
  service: StatementWorkService;
  run: Run;
}) {
  const confirmed = work.settings.evidence[doc.job_id];
  const period = doc.source_period?.document_coverage
    ? doc.source_period
    : null;
  const [account, setAccount] = useState(doc.account_id || "");
  const [evidence, setEvidence] = useState<WorkEvidence>(
    confirmed ?? {
      identity_confirmed: false,
      start_date: period?.start_date ?? "",
      end_date: period?.end_date ?? "",
      empty_confirmed: false,
      balance: null,
    },
  );
  const exclusion = work.settings.exclusions[doc.job_id];
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(() =>
      service.evidence(work, doc.job_id, {
        ...evidence,
        start_date: evidence.start_date || null,
        end_date: evidence.end_date || null,
      }),
    );
  };
  return (
    <article className={styles.conversion} aria-label={doc.filename}>
      <h3>{doc.filename}</h3>
      <p>
        {doc.expired
          ? "Arquivo expirado"
          : doc.status === "completed"
            ? doc.reviewed_at
              ? "Revisão confirmada"
              : "Revisão pendente"
            : "Processamento pendente ou com falha"}
      </p>
      <div className={styles.actions}>
        {doc.review_url && (
          <Link className="secondary-button" href={doc.review_url}>
            Abrir revisão
          </Link>
        )}
        {doc.report_url && (
          <a className="secondary-button" href={doc.report_url}>
            {doc.report_format === "xlsx"
              ? "Excel do arquivo completo"
              : "OFX do arquivo completo"}
          </a>
        )}
      </div>
      {exclusion && (
        <p role="status">Excluído deste trabalho: {exclusion.reason}</p>
      )}
      {work.state === "open" && (
        <>
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              void run(() =>
                service.attach(
                  work,
                  doc.job_id,
                  account || null,
                  doc.context_version,
                ),
              );
            }}
          >
            <label>
              Conta de origem de {doc.filename}
              <select
                value={account}
                onChange={(event) => setAccount(event.target.value)}
              >
                <option value="">Conta a confirmar</option>
                {work.accounts.map((a) => (
                  <option value={a.id} key={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <button className="secondary-button">
              Salvar conta do extrato
            </button>
          </form>
          {!doc.expired && !exclusion && (
            <form
              className={styles.form}
              aria-label={`Conferir ${doc.filename}`}
              onSubmit={submit}
            >
              <label className={styles.checkbox}>
                <input
                  type="checkbox"
                  checked={evidence.identity_confirmed}
                  onChange={(event) =>
                    setEvidence({
                      ...evidence,
                      identity_confirmed: event.target.checked,
                    })
                  }
                />
                Conferi o titular e a conta de origem
              </label>
              <p>
                Informe o intervalo declarado no documento. Datas dos
                lançamentos, sozinhas, não comprovam cobertura.
              </p>
              <label>
                Início do documento
                <input
                  type="date"
                  value={evidence.start_date || ""}
                  onChange={(event) =>
                    setEvidence({ ...evidence, start_date: event.target.value })
                  }
                />
              </label>
              <label>
                Fim do documento
                <input
                  type="date"
                  min={evidence.start_date || undefined}
                  value={evidence.end_date || ""}
                  onChange={(event) =>
                    setEvidence({ ...evidence, end_date: event.target.value })
                  }
                />
              </label>
              <label className={styles.checkbox}>
                <input
                  type="checkbox"
                  checked={Boolean(evidence.empty_confirmed)}
                  onChange={(event) =>
                    setEvidence({
                      ...evidence,
                      empty_confirmed: event.target.checked,
                    })
                  }
                />
                Confirmei que o extrato não tem movimentação
              </label>
              {evidence.empty_confirmed && (
                <label>
                  Saldo confirmado (se não identificado no documento)
                  <input
                    type="number"
                    step="0.01"
                    value={evidence.balance ?? ""}
                    onChange={(event) =>
                      setEvidence({
                        ...evidence,
                        balance:
                          event.target.value === ""
                            ? null
                            : Number(event.target.value),
                      })
                    }
                  />
                </label>
              )}
              <button className="secondary-button">
                Salvar conferência documental
              </button>
            </form>
          )}
          <ReasonForm
            label={
              exclusion
                ? "Motivo da restauração"
                : "Motivo da exclusão deste trabalho"
            }
            button={
              exclusion ? "Restaurar no trabalho" : "Excluir deste trabalho"
            }
            onSubmit={(reason) =>
              void run(() =>
                service.exclude(work, doc.job_id, reason, !exclusion),
              )
            }
          />
        </>
      )}
    </article>
  );
}

export function WorkDetail({
  work,
  service,
  organization,
  run,
}: {
  work: StatementWork;
  service: StatementWorkService;
  organization: OrganizationService;
  run: Run;
}) {
  const [tab, setTab] = useState(0);
  const [scopeDraft, setScopeDraft] = useState({
    version: work.version,
    ids: work.settings.account_ids,
  });
  const scope =
    scopeDraft.version === work.version
      ? scopeDraft.ids
      : work.settings.account_ids;
  const setScope = (update: (ids: string[]) => string[]) =>
    setScopeDraft({ version: work.version, ids: update(scope) });
  const [candidates, setCandidates] = useState<OrganizedConversion[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [accountDraft, setAccountDraft] = useState({
    name: "",
    bank_code: "",
    branch: "",
    account_number: "",
  });
  const [remarks, setRemarks] = useState<Record<string, string>>({});
  const [acknowledgedFor, setAcknowledgedFor] = useState<string | null>(null);
  const acknowledged = acknowledgedFor === work.fingerprint;
  const setAcknowledged = (value: boolean) =>
    setAcknowledgedFor(value ? work.fingerprint : null);
  const [page, setPage] = useState(0);
  const accountNameFor = (id: string | null) =>
    work.accounts.find((a) => a.id === id)?.name ?? "Período do trabalho";
  const loadCandidates = async (next?: string) => {
    setCandidateError(null);
    try {
      const result = await organization.listConversions(
        { client: "", period: "", account: "" },
        next,
      );
      setCandidates(result.items);
      setCursor(result.next_cursor);
    } catch (error) {
      setCandidateError(
        error instanceof Error
          ? error.message
          : "Não foi possível consultar arquivos.",
      );
    }
  };
  return (
    <>
      <div className={styles.browserHeader}>
        <div>
          <h1>
            {work.client.name} · {work.period.name}
          </h1>
          <p>
            {formatDate(work.period.start_date)} a{" "}
            {formatDate(work.period.end_date)}
          </p>
        </div>
        <Link className="secondary-button" href="/works">
          Todos os trabalhos
        </Link>
      </div>
      <p>
        {work.state === "completed"
          ? work.closings[0]?.snapshot.conclusion === "with_reservations"
            ? "Concluído com ressalvas"
            : "Concluído"
          : "Em preparação"}{" "}
        · {work.assessment.accounts_count} conta(s) no escopo ·{" "}
        {work.assessment.documents_count} extrato(s) ·{" "}
        {work.assessment.blockers.length} bloqueio(s) ·{" "}
        {work.assessment.reservations.length} ressalva(s)
      </p>
      {work.needs_reassessment && (
        <p role="alert" className={styles.error}>
          Os documentos ou vínculos mudaram após a conclusão. A versão concluída
          foi preservada; reabra o trabalho para reavaliar.
        </p>
      )}
      <nav className={styles.tabs} aria-label="Etapas do trabalho">
        {tabs.map((label, index) => (
          <button
            key={label}
            type="button"
            aria-pressed={tab === index}
            className={tab === index ? "primary-button" : "secondary-button"}
            onClick={() => setTab(index)}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === 0 && (
        <section aria-label="Pendências do trabalho">
          <h2>Conferência atual</h2>
          {work.assessment.blockers.map((issue, index) => (
            <article className={styles.catalogItem} key={`b${index}`}>
              <div>
                <strong>Bloqueio · {accountNameFor(issue.account_id)}</strong>
                <p>{issue.message}</p>
              </div>
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  setTab(issue.code === "possible_duplicate" ? 2 : 1)
                }
              >
                Resolver
              </button>
              {issue.job_id &&
                work.documents.find((d) => d.job_id === issue.job_id)
                  ?.review_url && (
                  <Link
                    href={
                      work.documents.find((d) => d.job_id === issue.job_id)!
                        .review_url!
                    }
                  >
                    Abrir revisão
                  </Link>
                )}
            </article>
          ))}
          {work.assessment.reservations.map((issue, index) => (
            <article className={styles.catalogItem} key={`r${index}`}>
              <div>
                <strong>Ressalva · {accountNameFor(issue.account_id)}</strong>
                <p>{issue.message}</p>
              </div>
              <button
                type="button"
                className="secondary-button"
                onClick={() => setTab(1)}
              >
                Ver cobertura
              </button>
            </article>
          ))}
          {!work.assessment.blockers.length &&
            !work.assessment.reservations.length && (
              <p>Conferência pronta para conclusão. Confira o resumo final.</p>
            )}
        </section>
      )}
      {tab === 1 && (
        <section aria-label="Contas e cobertura do trabalho">
          <h2>Contas esperadas</h2>
          <p>Contas fora do escopo não geram pendências deste trabalho.</p>
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              void run(() => service.scope(work, scope));
            }}
          >
            <fieldset disabled={work.state === "completed"}>
              {work.accounts.map((account) => (
                <label className={styles.checkbox} key={account.id}>
                  <input
                    type="checkbox"
                    checked={scope.includes(account.id)}
                    onChange={(event) =>
                      setScope((previous) =>
                        event.target.checked
                          ? [...previous, account.id]
                          : previous.filter((id) => id !== account.id),
                      )
                    }
                  />
                  {account.name}
                </label>
              ))}
              <button className="primary-button" disabled={!scope.length}>
                Confirmar contas do trabalho
              </button>
            </fieldset>
          </form>
          {work.settings.scope_confirmed_at && (
            <p>
              Escopo confirmado em{" "}
              {formatDate(work.settings.scope_confirmed_at)}. Alterações ficam
              registradas no histórico.
            </p>
          )}
          {work.state === "open" && (
            <details>
              <summary>Cadastrar outra conta deste cliente</summary>
              <form
                className={styles.form}
                onSubmit={(event) => {
                  event.preventDefault();
                  void run(async () => {
                    await organization.saveAccount({
                      ...accountDraft,
                      client_id: work.client.id,
                    });
                    return service.get(work.id);
                  });
                }}
              >
                {(
                  [
                    ["name", "Nome da nova conta"],
                    ["bank_code", "Código do banco"],
                    ["branch", "Agência"],
                    ["account_number", "Número da conta"],
                  ] as const
                ).map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <input
                      required={key === "name"}
                      maxLength={key === "name" ? 160 : 80}
                      value={accountDraft[key]}
                      onChange={(event) =>
                        setAccountDraft({
                          ...accountDraft,
                          [key]: event.target.value,
                        })
                      }
                    />
                  </label>
                ))}
                <button className="secondary-button">Cadastrar conta</button>
              </form>
            </details>
          )}
          {work.assessment.coverage.map((item) => (
            <article key={item.account_id} className={styles.conversion}>
              <h3>
                {accountNameFor(item.account_id)} · {coverageLabel[item.status]}
              </h3>
              <p>{item.documents_count} extrato(s)</p>
              {item.gaps.map((gap, index) => (
                <p key={`g${index}`}>
                  Lacuna: {formatDate(gap.start_date)} a{" "}
                  {formatDate(gap.end_date)}
                </p>
              ))}
              {item.overlaps.map((span, index) => (
                <p key={`o${index}`}>
                  Sobreposição: {formatDate(span.start_date)} a{" "}
                  {formatDate(span.end_date)}
                </p>
              ))}
            </article>
          ))}
          <h2>Conferência documental</h2>
          <div className={styles.conversions}>
            {work.documents.map((doc) => (
              <DocumentEditor
                key={`${doc.job_id}:${work.version}`}
                work={work}
                doc={doc}
                service={service}
                run={run}
              />
            ))}
          </div>
        </section>
      )}
      {tab === 2 && (
        <section aria-label="Extratos e movimentações do trabalho">
          <div className={styles.browserHeader}>
            <h2>Extratos existentes</h2>
            {work.state === "open" && (
              <>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => void loadCandidates()}
                >
                  Selecionar conversões existentes
                </button>
                <Link
                  href={`/conversions?work=${encodeURIComponent(work.id)}`}
                  className="secondary-button"
                >
                  Converter novos arquivos
                </Link>
              </>
            )}
          </div>
          {candidateError && <p role="alert">{candidateError}</p>}
          {candidates
            .filter(
              (candidate) =>
                !work.documents.some((d) => d.job_id === candidate.job_id),
            )
            .map((item) => (
              <article className={styles.catalogItem} key={item.job_id}>
                <strong>{item.filename}</strong>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={Boolean(
                    item.context.client_id &&
                    item.context.client_id !== work.client.id,
                  )}
                  onClick={() =>
                    void run(() =>
                      service.attach(
                        work,
                        item.job_id,
                        item.context.account_id,
                        item.context.version,
                      ),
                    )
                  }
                >
                  Vincular ao trabalho
                </button>
              </article>
            ))}
          {cursor && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => void loadCandidates(cursor)}
            >
              Próxima página de arquivos
            </button>
          )}
          <div className={styles.conversions}>
            {work.documents.map((doc) => (
              <article className={styles.conversion} key={doc.job_id}>
                <h3>{doc.filename}</h3>
                <p>
                  {work.settings.exclusions[doc.job_id]
                    ? "Excluído deste trabalho"
                    : accountNameFor(doc.account_id)}
                </p>
                {doc.review_url && (
                  <Link href={doc.review_url}>Abrir revisão</Link>
                )}
              </article>
            ))}
          </div>
          {work.assessment.duplicate_groups.map((group) => (
            <article className={styles.conversion} key={group.id}>
              <h3>
                Possível duplicidade ·{" "}
                {group.resolved
                  ? "Conferida como movimentos distintos"
                  : "Pendente"}
              </h3>
              {group.items.map((item, index) => (
                <p key={index}>
                  {
                    work.documents.find((d) => d.job_id === item.job_id)
                      ?.filename
                  }{" "}
                  · {formatDate(item.date)} · {item.description} ·{" "}
                  {item.amount.toLocaleString("pt-BR", {
                    minimumFractionDigits: 2,
                  })}
                </p>
              ))}
              {!group.resolved && work.state === "open" && (
                <ReasonForm
                  label="Evidência de que são movimentos distintos"
                  button="Confirmar movimentos distintos"
                  onSubmit={(reason) =>
                    void run(() => service.distinct(work, group.id, reason))
                  }
                />
              )}
            </article>
          ))}
          <h2>Movimentações no recorte do trabalho</h2>
          <p>
            {work.assessment.movements_count} lançamento(s). Movimentação
            bancária não é classificação de receitas ou despesas.
          </p>
          <div className="history-table-scroll">
            <table className="batch-history-table">
              <thead>
                <tr>
                  <th>Data</th>
                  <th>Conta</th>
                  <th>Descrição</th>
                  <th>Valor</th>
                </tr>
              </thead>
              <tbody>
                {work.assessment.movements
                  .slice(page * 50, (page + 1) * 50)
                  .map((item, index) => (
                    <tr key={`${item.job_id}:${item.transaction_id}:${index}`}>
                      <td>{formatDate(item.date)}</td>
                      <td>{accountNameFor(item.account_id)}</td>
                      <td>{item.description}</td>
                      <td>
                        {item.amount.toLocaleString("pt-BR", {
                          minimumFractionDigits: 2,
                        })}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <div className={styles.pagination}>
            <button
              type="button"
              disabled={!page}
              onClick={() => setPage(page - 1)}
            >
              Anterior
            </button>
            <span>Página {page + 1}</span>
            <button
              type="button"
              disabled={(page + 1) * 50 >= work.assessment.movements.length}
              onClick={() => setPage(page + 1)}
            >
              Próxima
            </button>
          </div>
        </section>
      )}
      {tab === 3 && (
        <section aria-label="Resumo do fechamento">
          <h2>Conferência final</h2>
          <p>
            Recorte: {formatDate(work.period.start_date)} a{" "}
            {formatDate(work.period.end_date)}. A conclusão não renova a
            disponibilidade dos arquivos.
          </p>
          {work.state === "open" ? (
            <form
              className={styles.form}
              onSubmit={(event) => {
                event.preventDefault();
                void run(() => service.complete(work, remarks));
              }}
            >
              {work.assessment.blockers.length > 0 && (
                <p role="alert">
                  Existem bloqueios técnicos. Ressalvas não substituem sua
                  correção.
                </p>
              )}
              {[
                ...new Set(
                  work.assessment.reservations.map(
                    (issue) => issue.account_id || "period",
                  ),
                ),
              ].map((key) => (
                <label key={key}>
                  Ressalvas de {accountNameFor(key)}
                  <textarea
                    required
                    minLength={3}
                    maxLength={2000}
                    value={remarks[key] || ""}
                    onChange={(event) =>
                      setRemarks({ ...remarks, [key]: event.target.value })
                    }
                  />
                </label>
              ))}
              <label className={styles.checkbox}>
                <input
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                />
                Conferi o escopo, os documentos, a revisão e as ressalvas
              </label>
              <button
                className="primary-button"
                disabled={!acknowledged || work.assessment.blockers.length > 0}
              >
                Concluir trabalho
              </button>
            </form>
          ) : (
            <>
              {!work.needs_reassessment && (
                <a
                  className="primary-button"
                  href={`/api/pro/works/${encodeURIComponent(work.id)}/export`}
                >
                  Baixar movimentações do recorte (CSV)
                </a>
              )}
              <ReasonForm
                label="Motivo da reabertura"
                button="Reabrir trabalho"
                onSubmit={(reason) =>
                  void run(() => service.reopen(work, reason))
                }
              />
            </>
          )}
          <h2>Versões concluídas</h2>
          {!work.closings.length && <p>Nenhuma versão concluída.</p>}
          {work.closings.map((closing) => (
            <details className={styles.conversion} key={closing.revision}>
              <summary>
                Versão {closing.revision} ·{" "}
                {closing.snapshot.conclusion === "with_reservations"
                  ? "Concluído com ressalvas"
                  : "Concluído"}{" "}
                · {formatDate(closing.created_at)}
              </summary>
              <p>
                {closing.snapshot.client.name} · {closing.snapshot.period.name}{" "}
                · {formatDate(closing.snapshot.period.start_date)} a{" "}
                {formatDate(closing.snapshot.period.end_date)}. Registrada por{" "}
                {closing.created_by}.{" "}
                {closing.snapshot.assessment.documents_count} extrato(s),{" "}
                {closing.snapshot.assessment.movements_count} movimento(s) no
                recorte.
              </p>
              {Object.entries(closing.snapshot.remarks).map(([key, value]) => (
                <p key={key}>
                  {closing.snapshot.accounts?.find((a) => a.id === key)?.name ||
                    accountNameFor(key)}
                  : {value}
                </p>
              ))}
            </details>
          ))}
          <details>
            <summary>Histórico de alterações</summary>
            {work.events.map((event, index) => (
              <p key={index}>
                {formatDate(event.created_at)} ·{" "}
                {(
                  {
                    created: "Trabalho criado",
                    scope_confirmed: "Contas confirmadas",
                    document_linked: "Extrato vinculado",
                    evidence: "Conferência documental",
                    exclusions: "Escopo do documento alterado",
                    duplicate_resolutions: "Duplicidade conferida",
                    completed: "Trabalho concluído",
                    reopened: "Trabalho reaberto",
                  } as Record<string, string>
                )[event.event] || "Alteração registrada"}{" "}
                · {event.created_by}
              </p>
            ))}
          </details>
        </section>
      )}
    </>
  );
}
