# Extratos com várias contas

A estrutura compartilhada mantém a conta titular, o bloco, as páginas originais e os controles financeiros de cada lançamento. A primeira habilitação é para o Santander Consolidado Inteligente, com resumos em páginas distintas identificados por agência, conta, titular e mês. Dados de favorecidos e seções de investimentos não iniciam uma conta. Titulares diferentes ou várias contas sem um limite de bloco inequívoco continuam exigindo tratamento separado; não são associados por suposição.

`PDF_MULTI_ACCOUNT_ENABLED=false` é o padrão. A chave `true` habilita somente esse leitor Santander, em PDF nativo e OCR. Os demais layouts mantêm sua proteção atual.

## Implantação compatível

1. Manter a chave desligada e publicar as novas versões da API, do worker e dos frontends. O script público usa uma nova versão de cache; o Pro utiliza os assets versionados pelo build. Os novos leitores de resultados continuam compatíveis com os JSONs antigos, sem `statement_account`.
2. Confirmar que todas as instâncias que consultam, editam ou regeneram resultados estão na nova versão. A chave não deve ser habilitada enquanto uma API antiga ainda puder editar um resultado novo.
3. Habilitar `PDF_MULTI_ACCOUNT_ENABLED=true` na configuração dos processos que convertem PDFs, incluindo API e worker. Essa configuração de produção é feita no ambiente de implantação; este PR não altera a AWS.
4. Converter um consolidado e conferir as contas nas linhas de prévia/JSON, nas colunas do CSV/Excel e nos elementos `STMTTRNRS` separados do OFX. Os saldos globais e a conta global devem ficar vazios. Cada bloco tem seus próprios controles.
5. Acompanhar falhas/revisões do Santander, cobertura de páginas, associação das linhas e avisos de continuidade por conta. O arquivo de abril auditado conserva o aviso de cobertura: são 26 páginas físicas para uma paginação impressa de 27.

## Edição e exportação

Os campos novos são opcionais e aditivos no resultado temporário; não há migração de banco. CSV/Excel acrescentam a identidade da conta apenas nesses documentos. O OFX contém uma resposta de extrato para cada bloco identificado, mantendo conta, agência e saldo separados. Nenhum saldo global é calculado entre contas.

Dados de conta e saldo únicos não podem sobrescrever um consolidado. Inserções exigem um lançamento de referência para estabelecer a conta de origem. Edições mantêm essa associação e exigem revisão dos controles do bloco alterado; seu saldo impresso não é exportado como saldo conciliado. Os outros blocos permanecem separados.

## Desativação e rollback

Desligar a chave interrompe novas conversões com várias contas, mas mantém leitura, edição e exportação dos resultados já gerados. Não exige reescrita de dados.

Para retornar a uma versão anterior à introdução de `statement_account`, desligar a chave, aguardar os trabalhos em andamento e manter a nova API atendendo os resultados existentes até expirarem pelo TTL configurado (24 horas no padrão). Não direcionar resultados com contas separadas a um exportador antigo: ele não preserva os campos novos. Se for necessário um rollback imediato dos workers, a API nova deve permanecer para servir os resultados existentes.
