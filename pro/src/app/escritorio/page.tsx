import Link from "next/link";

export default function OfficeOffer() {
  return (
    <main className="standard-page">
      <h1>OFX Simples Escritório</h1>
      <p>
        Receba documentos financeiros, organize por cliente e mês, confira
        movimentos e prepare um pacote para a contabilidade.
      </p>
      <p>
        Piloto assistido. Preço e quantidade de conversões serão definidos antes
        da contratação. Não há cobrança ou renovação automática por esta página.
      </p>
      <ul>
        <li>
          Uma conversão por PDF transformado em dados estruturados, dentro dos
          limites contratados. Arquivos com falha não consomem conversões.
        </li>
        <li>
          Importações de OFX, CSV e Excel (.xlsx), organização, conferência e
          novos downloads não consomem conversões.
        </li>
        <li>
          Extratos com revisão bancária e documentos de apoio com conferência
          manual separada.
        </li>
        <li>
          ZIP com OFX e planilhas quando disponíveis, originais, páginas
          separadas quando identificadas e manifesto de pendências.
        </li>
      </ul>
      <p>
        Limite de 10 MB por arquivo. Os limites de páginas, OCR, arquivos por
        envio e conversões são informados na contratação e em Conta e plano. O
        pacote aceita até 30 arquivos e 64 MB.
      </p>
      <p>
        O prazo de acesso é o informado em cada envio. Um novo envio ou download
        não prolonga o prazo dos arquivos anteriores. Baixe o pacote antes da
        expiração; verificação manual não preserva arquivos permanentemente.
      </p>
      <p>
        Não inclui conciliação contábil, apuração fiscal ou garantia de
        importação em qualquer ERP. Documentos incertos precisam de conferência;
        notas e comprovantes não criam movimentos no extrato.
      </p>
      <p>
        O pagamento e a habilitação do piloto são assistidos. O suporte confirma
        preço, cota e prazo antes do pagamento. Acesso, término e cancelamento
        são controlados pelo plano contratado.
      </p>
      <p>
        <a href="https://www.ofxsimples.com.br/contato.html">
          Falar com suporte sobre contratação, cancelamento ou exclusão
        </a>
      </p>
      <p>
        <a href="https://www.ofxsimples.com.br/termos-de-uso.html">
          Termos de Uso
        </a>
        {" · "}
        <a href="https://www.ofxsimples.com.br/politica-de-privacidade.html">
          Privacidade
        </a>
      </p>
      <p>
        <Link href="/account">Conta e plano</Link>
        {" · "}
        <Link href="/documents">Acessar documentos</Link>
      </p>
    </main>
  );
}
