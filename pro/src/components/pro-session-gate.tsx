"use client";

import { LoaderCircle, LockKeyhole } from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  createProAuthService,
  type ProAuthService,
  type ProUser,
} from "@/data/pro-auth-service";

export function ProSessionGate({
  authService,
  children,
}: {
  authService?: ProAuthService;
  children: ReactNode;
}) {
  const defaultService = useMemo(
    () =>
      createProAuthService({
        apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
      }),
    [],
  );
  const activeService = authService ?? defaultService;
  const [status, setStatus] = useState<
    "checking" | "anonymous" | "authenticated"
  >("checking");
  const [user, setUser] = useState<ProUser | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void activeService
      .currentUser()
      .then((currentUser) => {
        if (!active) return;
        setUser(currentUser);
        setStatus(currentUser ? "authenticated" : "anonymous");
      })
      .catch((caught) => {
        if (!active) return;
        setError(
          caught instanceof Error
            ? caught.message
            : "Não foi possível verificar sua sessão.",
        );
        setStatus("anonymous");
      });
    return () => {
      active = false;
    };
  }, [activeService]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const authenticatedUser = await activeService.login(email, password);
      setPassword("");
      setUser(authenticatedUser);
      setStatus("authenticated");
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Não foi possível entrar no OFX Simples PRO.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (status === "checking") {
    return (
      <main className="standard-page session-gate-page" aria-busy="true">
        <LoaderCircle className="spin" size={24} />
        <p>Verificando sua sessão segura…</p>
      </main>
    );
  }

  if (status === "authenticated" && user) return children;

  return (
    <main className="standard-page session-gate-page">
      <section className="session-gate-card">
        <span className="placeholder-icon">
          <LockKeyhole size={24} />
        </span>
        <div>
          <span className="eyebrow">Acesso seguro</span>
          <h1>Entre no OFX Simples PRO</h1>
          <p>
            Use sua conta cadastrada para validar o plano e liberar a conversão
            em lote.
          </p>
        </div>

        <form
          className="session-login-form"
          onSubmit={(event) => void submit(event)}
        >
          <label htmlFor="pro-login-email">E-mail</label>
          <input
            id="pro-login-email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />

          <label htmlFor="pro-login-password">Senha</label>
          <input
            id="pro-login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />

          {error && (
            <p className="upload-error" role="alert">
              {error}
            </p>
          )}

          <button
            className="primary-button"
            type="submit"
            disabled={submitting}
          >
            {submitting && <LoaderCircle className="spin" size={16} />}
            {submitting ? "Entrando…" : "Entrar"}
          </button>
        </form>

        <p className="session-security-note">
          Sua sessão fica protegida em cookies HttpOnly. Nenhum token é exposto
          ao navegador.
        </p>
      </section>
    </main>
  );
}
