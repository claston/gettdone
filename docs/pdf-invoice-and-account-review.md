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

No PRO, `partial_statement` é um aviso do arquivo: mantém o status **Revisar**,
mas não torna suspeitos a descrição e o valor da primeira linha. O aviso geral
era registrado nessa linha por compatibilidade com o contrato de conversão.
Outros alertas do lançamento continuam sendo considerados, e uma revisão já
confirmada continua válida.

No extrato do Aplicativo Santander Empresas, a extração pode unir a data à
descrição e dobrar descrições em várias linhas. As regiões de origem agora
aceitam diferenças de espaços, exigindo os mesmos caracteres do PDF original.
Quando a ordem completa da página coincide, ela desambigua linhas repetidas;
caso contrário, o trecho precisa ser único e respeitar os limites das linhas
nativas. A rotação é aplicada às coordenadas depois da leitura. Não há associação
por semelhança de nomes ou valores, nem alteração de datas, valores ou saldos.

A busca no visualizador também ignora espaços introduzidos ou omitidos entre
fragmentos de texto, incluindo letras acentuadas separadas e nomes quebrados.
As caixas destacadas continuam vindo dos fragmentos originais. Fontes que
informam ascensão e descida zeradas usam a posição e a altura do próprio trecho
de texto; essas métricas incompletas não fazem mais o destaque desaparecer.

O frontend corrigido reconhece os avisos de extrato parcial já salvos. As novas
coordenadas exigem o processamento pelo worker atualizado; conversões antigas
sem regiões precisam ser refeitas. Não há reprocessamento automático nem
migração dos lotes existentes, e o PDF original não é modificado.

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
