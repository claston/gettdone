import { ArrowRight, Plus, Sparkles } from "lucide-react";
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
        <Link href="/conversions" className="primary-button">
          <Plus size={16} /> Nova conversão
        </Link>
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
        <Link
          href="/demo/review/batch-setembro-2026"
          className="primary-button"
        >
          Abrir workspace de demonstração <ArrowRight size={16} />
        </Link>
      </section>
    </main>
  );
}
