import { type NextRequest } from "next/server";

function ascii(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\x20-\x7E]/g, "");
}

function streamForPage(bank: string, page: number): string {
  const lines = [
    `EXTRATO DE CONTA CORRENTE - ${bank.toUpperCase()}`,
    `Periodo: 01/09/2026 a 30/09/2026                         Pagina ${page} de 3`,
    "Data       Historico                                  Documento       Valor        Saldo",
    "02/09/26   PIX RECEBIDO - COMERCIO HORIZONTE          846219        145,90 C    38.566,07",
    "03/09/26   PAGAMENTO DE BOLETO                         293104        283,13 D    38.282,94",
    "04/09/26   TRANSFERENCIA ENTRE CONTAS                  714992        420,36 D    37.862,58",
    "05/09/26   TARIFA PACOTE DE SERVICOS                   000000        557,59 D    37.304,99",
    "06/09/26   RECEBIMENTO VIA CARTAO                      592831        694,82 C    37.999,81",
    "09/09/26   PIX ENVIADO - FORNECEDOR DELTA              184621        832,05 D    37.167,76",
    "10/09/26   APLICACAO AUTOMATICA                        830177        969,28 D    36.198,48",
    "11/09/26   RESGATE DE INVESTIMENTO                     264185      1.106,51 C    37.304,99",
    "12/09/26   TED RECEBIDA                                619303      1.243,74 C    38.548,73",
    "13/09/26   DEBITO AUTOMATICO - ENERGIA                 730421      1.380,97 D    37.167,76",
    "",
    "Resumo do periodo",
    "Saldo anterior: 38.420,17        Total creditos: 3.190,97        Total debitos: 4.443,38",
    "Saldo final: 37.167,76",
  ];
  return [
    "BT",
    "/F1 15 Tf",
    "56 780 Td",
    ...lines.flatMap((line, index) => [
      index === 0 ? "" : "0 -34 Td",
      `(${ascii(line).replace(/[()\\]/g, "\\$&")}) Tj`,
    ]),
    "ET",
  ]
    .filter(Boolean)
    .join("\n");
}

function createPdf(bank: string): Uint8Array {
  const objects: string[] = [];
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = "<< /Type /Pages /Kids [4 0 R 6 0 R 8 0 R] /Count 3 >>";
  objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";
  for (let page = 1; page <= 3; page += 1) {
    const pageId = page === 1 ? 4 : page === 2 ? 6 : 8;
    const contentId = pageId + 1;
    const stream = streamForPage(bank, page);
    const length = new TextEncoder().encode(stream).length;
    objects[pageId] =
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >> /Contents ${contentId} 0 R >>`;
    objects[contentId] =
      `<< /Length ${length} >>\nstream\n${stream}\nendstream`;
  }

  let output = "%PDF-1.4\n%mock\n";
  const offsets = [0];
  for (let id = 1; id < objects.length; id += 1) {
    offsets[id] = new TextEncoder().encode(output).length;
    output += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xref = new TextEncoder().encode(output).length;
  output += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let id = 1; id < objects.length; id += 1)
    output += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  output += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return new TextEncoder().encode(output);
}

export async function GET(request: NextRequest) {
  const bank =
    request.nextUrl.searchParams.get("bank") ?? "Banco demonstrativo";
  const bytes = createPdf(bank);
  const body = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(body).set(bytes);
  return new Response(body, {
    headers: {
      "Content-Type": "application/pdf",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
