"use client";

import {
  BarChart3,
  Building2,
  ChevronLeft,
  ChevronRight,
  FileStack,
  LogOut,
  Menu,
  Settings,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { useProSession } from "@/components/pro-session-gate";
import type { ProUser } from "@/data/pro-auth-service";

const navigation = [
  { href: "/dashboard", label: "Extratos recebidos", icon: BarChart3 },
  { href: "/conversions", label: "Conversões", icon: FileStack },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const pathname = usePathname();
  const { user, logout } = useProSession();
  const quota = formatQuota(user);

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="app-sidebar">
        <Link href="/conversions" className="app-brand">
          <span className="brand-icon">
            <Zap size={18} fill="currentColor" />
          </span>
          <span className="brand-copy">
            OFX Simples <b>PRO</b>
          </span>
        </Link>
        <button
          className="sidebar-toggle"
          onClick={() => setCollapsed((value) => !value)}
          aria-label={collapsed ? "Expandir menu" : "Recolher menu"}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
        <nav aria-label="Navegação principal">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? "active" : ""}
                title={collapsed ? item.label : undefined}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <Link href="/account">
            <Settings size={18} />
            <span>Conta e plano</span>
          </Link>
          <button
            disabled={loggingOut}
            onClick={() => {
              if (loggingOut) return;
              setLoggingOut(true);
              setLogoutError(null);
              void logout()
                .catch(() =>
                  setLogoutError("Não foi possível encerrar sua sessão."),
                )
                .finally(() => setLoggingOut(false));
            }}
          >
            <LogOut size={18} />
            <span>{loggingOut ? "Saindo…" : "Sair"}</span>
          </button>
          <div className="sidebar-profile">
            <span>{initials(user.name)}</span>
            <div>
              <strong>{user.name}</strong>
              <small>{planLabel(user)}</small>
            </div>
          </div>
        </div>
      </aside>
      <div className="app-content">
        <header className="app-topbar">
          <button className="mobile-menu" aria-label="Abrir menu">
            <Menu size={19} />
          </button>
          <span className="topbar-spacer" />
          {quota && (
            <span className="quota-top">
              <Building2 size={15} /> {quota}
            </span>
          )}
        </header>
        {logoutError && (
          <p className="app-shell-error" role="alert">
            {logoutError}
          </p>
        )}
        {children}
      </div>
    </div>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (
    parts
      .slice(0, 2)
      .map((part) => part[0]?.toLocaleUpperCase("pt-BR") ?? "")
      .join("") || "OF"
  );
}

function planLabel(user: ProUser): string {
  const plan = user.plan_name?.trim() || user.plan_code?.trim();
  return plan ? `Plano ${plan}` : "Plano ativo";
}

function formatQuota(user: ProUser): string | null {
  if (
    typeof user.quota_remaining !== "number" ||
    typeof user.quota_limit !== "number"
  ) {
    return null;
  }
  const used = Math.max(0, user.quota_limit - user.quota_remaining);
  const unit = user.quota_mode === "pages" ? "páginas" : "conversões";
  return `${used} de ${user.quota_limit} ${unit} usadas`;
}
