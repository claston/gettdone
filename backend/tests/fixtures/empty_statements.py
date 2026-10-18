"""Synthetic statements: no local bank documents or customer data required."""


def inactive_santander_pages() -> tuple[tuple[str, ...], ...]:
    bodies = (
        ("Santander Empresas", "EXTRATO CONSOLIDADO INTELIGENTE", "janeiro/2026"),
        ("Resumo - janeiro/2026", "Nome", "EMPRESA SINTETICA LTDA", "Agência", "0001",
         "Conta Corrente", "12.345678-9", "Pacote de Serviços",
         "Produtos e Serviços Quantidade Contratada", "TED - PIX 6"),
        ("Produtos e Serviços Quantidade Contratada", "Valor da Mensalidade (R$) 339,00",
         "Status do Débito PENDENTE", "Valores Praticados no Pacote de Serviços",
         "25.000 a 49.999 254,25", "Índices Econômicos / Financeiros",
         "CDI JANEIRO 1,16 14,26", "DOLAR COMERCIAL 31/01/2026 5,23 5,50"),
        ("Você e Seu Dinheiro", "Cuidado com o Golpe do Boleto Falso"),
    )
    return tuple(("Extrato_PJ_A4_Inteligente 1.0 - 2/4/2024", "BALP_SYNTHETIC.PIM", f"Pagina: {i}/4", *lines)
                 for i, lines in enumerate(bodies, start=1))
