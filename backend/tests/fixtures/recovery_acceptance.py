"""Fictional ledgers with explicit expected movements, independent of parsers."""
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_oct20_followup_ocr import ocr


def grid_pdf(pages, scale=1):
    resources = PdfReader(BytesIO(generate_native_text_pdf((("Fictional",),)))).pages[0]["/Resources"]
    writer = PdfWriter()
    for cells in pages:
        page = writer.add_blank_page(width=1000, height=800)
        page[NameObject("/Resources")] = resources.clone(writer)
        stream = DecodedStreamObject()
        stream.set_data("\n".join(
            f"BT /F1 8 Tf 1 0 0 1 {x*1000} {(1-y)*800} Tm <{text.encode('cp1252').hex()}> Tj ET"
            for text, x, y in cells).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page.scale_by(scale)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def bradesco_monthly_native(scale=1, *, continuation_header=True, appendix=False):
    header = [("Extrato Mensal / Por Período Bradesco net empresa", .06, .03),
        ("Extrato de: Ag: 1234 | CC: 123456-7 | Entre 01/12/2026 e 31/12/2026", .06, .07),
        ("Data", .06, .12), ("Lançamento", .18, .12), ("Dcto.", .49, .12),
        ("Crédito (R$)", .62, .12), ("Débito (R$)", .73, .12), ("Saldo (R$)", .88, .12)]
    pages = [header + [
        ("30/11/2026", .06, .20), ("SALDO ANTERIOR", .18, .20), ("-100,00", .88, .20),
        ("01/12/2026", .06, .28), ("RECEBIMENTO CARTÃO", .18, .28),
        ("EMPRESA FICTÍCIA", .18, .295), ("1001", .49, .295), ("500,00", .64, .295), ("400,00", .88, .295),
        ("Folha 1/2", .88, .92)], (header if continuation_header else []) + [
        ("PAGTO ELETRON COBRANCA", .18, .20), ("EMPRESA TESTE", .18, .215),
        ("1002", .49, .215), ("-150,00", .75, .215), ("250,00", .88, .215),
        ("02/12/2026", .06, .30), ("TRANSFERENCIA PIX", .18, .30), ("DESTINO TESTE", .18, .315),
        ("1003", .49, .315), ("-150,00", .75, .315), ("100,00", .88, .315),
        ("Total", .18, .40), ("500,00", .64, .40), ("300,00", .75, .40), ("100,00", .88, .40),
        ("Folha 2/2", .88, .92)]]
    if appendix:
        pages[-1] += [("Últimos Lançamentos", .06, .55), ("03/03/2027", .06, .65),
            ("TRANSFERENCIA DE OUTRO PERIODO", .18, .65), ("9999", .49, .65),
            ("999,00", .64, .65), ("1.099,00", .88, .65)]
    return grid_pdf(pages, scale)


def bb_outside_header():
    return generate_native_text_pdf(((
        "Banco do Brasil", "Extrato de Conta Corrente", "Cliente: EMPRESA TESTE",
        "Agência 1234 Conta 12345-6", "Período: 01 a 07/10/2026", "Dia Lote Documento Histórico Valor",
        "25/09/2026 Saldo Anterior 100,00 (+)",
        "28/09/2026 13105 92801 Pix - Enviado 20,00 (-)", "28/09/2026 13105 Saldo do dia 80,00 (+)",
        "01/10/2026 13105 100101 Pix - Enviado 10,00 (-)", "01/10/2026 Saldo do dia 70,00 (+)",
        "07/10/2026 S A L D O 70,00 (+)", "Informações Adicionais", "Juros 999,00",
        "Lançamentos Futuros", "07/10/2026 Tarifas Pendentes 0,50 (-)",),))


def sicredi_combined(*, bad_amount=False, mismatch=False):
    header = [("Sicredi", .06, .02), ("Cooperativa: 1234 Conta Corrente: 12345-6", .06, .04),
        ("Dados referentes ao período 01/08/2026 a 31/08/2026.", .06, .06),
        ("Data", .06, .12), ("Descrição", .20, .12), ("Documento", .55, .12),
        ("Valor (R$)", .70, .12), ("Saldo (R$)", .86, .12)]
    return ocr([header + [
        ("SALDO", .06, .18), ("100,00", .88, .18),
        ("03/08/2026 PAGAMENTO PIX EMPRESA TESTE", .06, .25), ("PIX_DEB", .55, .25),
        ("-XX,00" if bad_amount else "-20,00", .72, .25), ("79,00" if mismatch else "80,00", .88, .25),
        ("03/08/2026", .06, .35), ("PAGAMENTO PIX EMPRESA TESTE", .20, .35),
        ("PIX_DEB", .55, .35), ("-20,00", .72, .35), ("60,00", .88, .35),
        ("04/08/2026 RECEBIMENTO PIX", .06, .45), ("PIX_CRED", .55, .45), ("50,00", .72, .45), ("110,00", .88, .45),
        ("Lançamentos Futuros", .06, .60), ("01/09/2026 PAGAMENTO", .06, .65), ("-999,00", .72, .65)]])


def xp_investment_pages(*, mismatch=False):
    return ["""Extrato da Conta Investimento
Data do extrato: 08/2026
Cliente: EMPRESA FICTÍCIA
Saldo atual(31/08/2026) R$ 130,00
LANÇAMENTOS FUTUROS -R$ 999,00
01/09/2026 (D+1) -R$ 999,00
Saldo anterior(31/07/2026) R$ 100,00
Transações no período
Movimentação Liquidação Histórico Valor Saldo
03/08/2026 05/08/2026 TAXA TESTE -R$ 20,00 R$ 130,00
02/08/2026 02/08/2026 RESGATE TESTE R$ 50,00 R$ %s
XP INVESTIMENTOS CORRETORA DE CÂMBIO, TÍTULOS E VALORES MOBILIÁRIOS S.A.
www.xpi.com.br
Gerado em: 01/09/2026""" % ("151,00" if mismatch else "150,00")]
