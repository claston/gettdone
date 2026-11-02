import type { Conversion } from "@/domain/workspace";
import type { FinancialScenario } from "@/domain/financial-center";

// Immutable synthetic evidence built from the fixture, never from edited rows.
// Standard PDF Helvetica / WinAnsi supports the Portuguese text used here.
export function financialDemoPdf(
  scenario: FinancialScenario,
  conversion: Conversion,
): Blob {
  const source = scenario.sources.find(
    (item) => item.conversionId === conversion.id,
  )!;
  const lines = [
    "DEMONSTRAÇÃO - DOCUMENTO FICTÍCIO",
    scenario.client.name,
    `${conversion.bankName} - Conta demonstrativa`,
    `Cobertura: ${source.coverage?.from} a ${source.coverage?.to}`,
    "Data                 Descrição                                                Valor (BRL)",
    ...conversion.transactions.map(
      (row) =>
        `${row.date}       ${row.description.slice(0, 47).padEnd(49)} ${((row.credit ?? 0) - (row.debit ?? 0)).toFixed(2).replace(".", ",")}`,
    ),
    ...(conversion.transactions.length
      ? []
      : ["Extrato sem movimentações no período documentado."]),
    "",
    "Valores fictícios para validação da experiência. Não utilizar como comprovante.",
  ];
  const escape = (text: string) =>
    text
      .replace(/[—–]/g, "-")
      .replace(/[^\x20-\xFF]/g, " ")
      .replace(/[()\\]/g, "\\$&");
  const stream = `BT\n/F1 10 Tf\n40 790 Td\n${lines.map((line, index) => `${index ? "0 -28 Td\n" : ""}(${escape(line)}) Tj`).join("\n")}\nET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 650 842] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let content = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(content.length);
    content += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const start = content.length;
  content += `xref\n0 6\n0000000000 65535 f \n${offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
    .join("")}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`;
  return new Blob([Uint8Array.from(content, (char) => char.charCodeAt(0))], {
    type: "application/pdf",
  });
}
