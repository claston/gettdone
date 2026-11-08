"use client";

import { CreditCard, UserRound } from "lucide-react";

import { useProSession } from "./pro-session-gate";
import styles from "./account-page.module.css";

const numberLabel = (value: number) => value.toLocaleString("pt-BR");
const isLimit = (value: number | undefined): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

export function AccountPage() {
  const { user } = useProSession();
  const plan = user.plan_name?.trim() || user.plan_code?.trim();
  const unit =
    user.quota_mode === "pages"
      ? "páginas"
      : user.quota_mode === "conversion"
        ? "conversões"
        : null;
  const limit = user.quota_limit;
  const remaining = user.quota_remaining;
  const usage =
    unit && isLimit(limit) && isLimit(remaining) && remaining <= limit
      ? [
          { label: "Limite do plano", value: limit },
          { label: "Utilizado", value: limit - remaining },
          { label: "Disponível", value: remaining },
        ]
      : null;

  return (
    <main className="standard-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">OFX Simples PRO</span>
          <h1>Conta e plano</h1>
          <p>Confira os dados da sua conta e os limites do seu plano.</p>
        </div>
      </div>

      <div className={styles.layout}>
        <section className={styles.card} aria-labelledby="account-heading">
          <h2 id="account-heading">
            <UserRound size={20} aria-hidden="true" /> Sua conta
          </h2>
          <dl className={styles.details}>
            <div>
              <dt>Nome</dt>
              <dd>{user.name.trim() || "Não informado"}</dd>
            </div>
            <div>
              <dt>E-mail</dt>
              <dd>{user.email.trim() || "Não informado"}</dd>
            </div>
          </dl>
        </section>

        <section className={styles.card} aria-labelledby="plan-heading">
          <h2 id="plan-heading">
            <CreditCard size={20} aria-hidden="true" /> Seu plano
          </h2>
          <dl className={styles.details}>
            <div>
              <dt>Plano</dt>
              <dd>{plan || "Plano não informado"}</dd>
            </div>
          </dl>
          <h3>Uso do plano</h3>
          {usage ? (
            <dl className={styles.usage}>
              {usage.map(({ label, value }) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>
                    {numberLabel(value)} {unit}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className={styles.unavailable}>
              Os dados de uso do plano não estão disponíveis no momento.
            </p>
          )}
          <dl className={styles.details}>
            <div>
              <dt>Tamanho máximo por arquivo</dt>
              <dd>
                {isLimit(user.max_upload_size_bytes)
                  ? `${numberLabel(user.max_upload_size_bytes / (1024 * 1024))} MB`
                  : "Não informado"}
              </dd>
            </div>
            <div>
              <dt>Limite de páginas por arquivo</dt>
              <dd>
                {isLimit(user.max_pages_per_file)
                  ? `${numberLabel(user.max_pages_per_file)} páginas`
                  : "Não informado"}
              </dd>
            </div>
          </dl>
        </section>
      </div>
    </main>
  );
}
