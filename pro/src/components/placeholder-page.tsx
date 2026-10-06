import { ArrowRight, FileStack, Plus, Sparkles } from "lucide-react";
import Link from "next/link";

export function PlaceholderPage({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <main className="standard-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">OFX Simples PRO</span>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        <button className="primary-button">
          <Plus size={16} /> Nova conversão
        </button>
      </div>
      <section className="placeholder-card">
        <span className="placeholder-icon">
          <Sparkles size={24} />
        </span>
        <h2>Área preparada para o próximo ciclo</h2>
        <p>
          Esta rota faz parte do shell navegável. Nesta fase, o foco está no
          workspace mockado de revisão.
        </p>
        <Link href="/review/batch-setembro-2026" className="primary-button">
          Abrir workspace de demonstração <ArrowRight size={16} />
        </Link>
      </section>
    </main>
  );
}

export function DashboardPage() {
  return (
    <main className="standard-page dashboard-page">
      <div className="page-title">
        <div>
          <span className="eyebrow">Segunda-feira, 5 de outubro</span>
          <h1>Bom trabalho, Ana</h1>
          <p>Veja o que precisa de atenção nos seus lotes.</p>
        </div>
        <button className="primary-button">
          <Plus size={16} /> Nova conversão
        </button>
      </div>
      <div className="metric-grid">
        <article>
          <span>Arquivos no mês</span>
          <strong>23</strong>
          <small>de 50 incluídos no plano</small>
        </article>
        <article>
          <span>Aguardando revisão</span>
          <strong className="warning-text">5</strong>
          <small>7 campos suspeitos</small>
        </article>
        <article>
          <span>Concluídos</span>
          <strong>18</strong>
          <small>78% do volume recebido</small>
        </article>
      </div>
      <section className="recent-batch">
        <div>
          <span className="eyebrow">Lote em andamento</span>
          <h2>Extratos Setembro/2026</h2>
          <p>Almeida & Torres Contabilidade · 8 arquivos</p>
        </div>
        <div className="batch-mini-progress">
          <span>
            <i style={{ width: "13%" }} />
          </span>
          <small>1 de 8 revisados</small>
        </div>
        <Link href="/review/batch-setembro-2026" className="primary-button">
          <FileStack size={16} /> Continuar revisão <ArrowRight size={16} />
        </Link>
      </section>
    </main>
  );
}
