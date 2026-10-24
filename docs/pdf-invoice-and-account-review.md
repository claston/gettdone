# Faturas Nubank, extratos parciais e tamanho da conta

A fatura Nubank usa `nubank_fatura_cartao_credito_v1`; o extrato da conta digital
continua usando `nubank_statement_ptbr`. A seleção da fatura exige a marca e a
estrutura de resumo e transações, antes da pontuação genérica por banco.

O leitor da fatura usa a data de vencimento para resolver o ano dos movimentos,
preserva as referências de cartão e exclui os subtotais do titular e de pagamentos.
Compras viram débitos; pagamentos e estornos viram créditos. O resumo impresso
confere pagamentos, compras e outros lançamentos, além do saldo anterior e do
total a pagar. Divergências aritméticas seguem a política existente de revisão;
datas inválidas e lançamentos sem valor continuam sendo erros de leitura.

O aviso do Santander de número máximo de consultas, acompanhado da orientação
para consultar todos os lançamentos, marca o documento como `partial_statement`.
Isso vale mesmo quando todas as páginas físicas estão presentes. Os movimentos
legíveis continuam disponíveis para revisão e exportação. HTML e PRO explicam
que o usuário deve conferir o período e obter o extrato consolidado completo.

O HTML aceita até 20 dígitos de conta, incluindo o verificador, com hífen opcional
e zeros iniciais preservados. Esse limite usa como referência o campo
`AccountNumber` do [DICT do Banco Central](https://github.com/bacen/pix-dict-api/blob/master/openapi/schemas.yaml),
cujo padrão é `^[0-9]{1,20}$`; não representa um limite universal de identificadores
bancários internacionais. A leitura do cabeçalho PDF também aceita 20 dígitos.

O campo de conta de destino do PRO já preservava números longos. A exportação
inicial dos PDFs agora usa agência, conta e banco identificados, inclusive no
worker do PRO. Correções explícitas continuam tendo prioridade. Relatórios
financeiros especializados continuam exigindo uma conta de destino informada
pelo usuário, sem reutilizar uma conta de origem do documento.

Não há mudança de schema nem migração. Arquivos OFX já gerados permanecem como
foram salvos; novas conversões e regenerações usam o comportamento atualizado.

As regressões sintéticas estão em `test_oct24_review_fixes.py` e
`test_oct24_review_http.py`, além dos testes do revisor HTML e do workspace PRO.
Os testes HTTP cobrem upload público, relatório, OFX e processamento pelo worker.
`POST /analyze` permanece desativado com HTTP 405; o upload público usa `/convert`.
