"use client";

import {
  BarChart3,
  Building2,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  FileStack,
  Landmark,
  LogOut,
  Menu,
  Settings,
  Users,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const navigation = [
  { href: "/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/conversions", label: "Conversões", icon: FileStack },
  { href: "/clients", label: "Clientes", icon: Users },
  { href: "/periods", label: "Competências", icon: CalendarDays },
  { href: "/reconciliation", label: "Conciliação", icon: Landmark, soon: true },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const pathname = usePathname();

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <aside className="app-sidebar">
        <Link href="/dashboard" className="app-brand">
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
                {item.soon && <em>Em breve</em>}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <Link href="/account">
            <Settings size={18} />
            <span>Conta e plano</span>
          </Link>
          <button>
            <LogOut size={18} />
            <span>Sair</span>
          </button>
          <div className="sidebar-profile">
            <span>AS</span>
            <div>
              <strong>Ana Souza</strong>
              <small>Plano Profissional</small>
            </div>
          </div>
        </div>
      </aside>
      <div className="app-content">
        <header className="app-topbar">
          <button className="mobile-menu" aria-label="Abrir menu">
            <Menu size={19} />
          </button>
          <span className="environment-chip">Ambiente demonstrativo</span>
          <span className="topbar-spacer" />
          <span className="quota-top">
            <Building2 size={15} /> 23 de 50 arquivos usados
          </span>
        </header>
        {children}
      </div>
    </div>
  );
}
