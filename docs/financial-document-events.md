# Eventos financeiros e comerciais

O processamento distingue a identidade do documento, os dados que ele registra e os formatos que podem representá-los. A classificação não implica que cada linha seja uma movimentação bancária.

## Cobertura inicial

| Tipo | Estrutura reconhecida | Eventos | Informações de controle | Exportação |
| --- | --- | --- | --- | --- |
| `receivables_report` | Títulos Pagos por Conta Crédito | Recebimento por título, com pagamento e valor cobrado | Valor original, vencimento, oscilação e totais | CSV, XLSX e OFX de recebimentos |
| `investment_statement` | Extrato de Investimentos CDB com aplicações, resgates e posições | Aplicação pelo principal; resgate pelo líquido | Posições, bruto, rendimentos, IOF, IRRF, taxa e vencimento | CSV e XLSX |
| `fiscal_summary` | NF-e, NFC-e e SAT (Resumo), observado no GDOOR | Registro fiscal, inclusive cancelamento/inutilização quando identificado | Chave, protocolo, modelo, direção fiscal, situação, quantidade e totais | CSV e XLSX |

As regras reconhecem essas estruturas, não todo documento emitido pelas instituições. Uma nota fiscal individual e uma posição de investimentos sem movimentos continuam seguindo suas políticas existentes. Outros layouts exigem um extrator apropriado.

## Contrato

`AnalysisData`, a resposta de conversão e o resultado estruturado recebem o campo opcional `financial_document`. Análises anteriores mantêm `null` e continuam usando o fluxo bancário existente. O contrato público está em `conversion/contracts/financial_documents.py`; sua versão inicial é `financial_events_v1`.

O documento contém classificação e evidências, origem quando identificável, representação do PDF, método de extração, metadados de titular/período/conta disponíveis, eventos, observações, validações e política de exportação. Metadados ausentes permanecem ausentes; a conta do produto de cobrança não é assumida como conta corrente de destino.

Cada evento preserva:

- Tipo, situação, referência e contraparte quando informada.
- Datas com papéis distintos: pagamento, vencimento, aplicação, resgate, vencimento do investimento e emissão.
- Valores monetários como strings decimais, calculados com `Decimal`.
- `amount` e `amount_basis`, que identificam o valor principal exibido.
- `cash_effect`, que permanece `null` para registros fiscais.
- Atributos específicos e referências físicas de página e linha.

Os IDs combinam a identidade do documento com a posição na origem; as linhas de prévia mantêm também as regiões do PDF. A posição inicial/final de investimentos é uma observação, não uma transação. No layout CDB com células em branco, apenas valores inequivocamente associados são nomeados; os demais valores impressos permanecem em `attributes.source_values`.

## Validação e apresentação

Os títulos são conferidos contra os três totais impressos e a diferença entre original e cobrado. O CDB confere totais das seções, líquido dos resgates e continuidade do principal. O resumo fiscal confere quantidade, total geral e totais de entrada/saída fiscal.

Diferenças de totais preservam os eventos extraídos e ficam explícitas nas validações; no relatório de títulos elas desabilitam OFX. Datas inválidas e linhas cuja associação não pode ser confirmada geram erro específico, sem tentativa de interpretar o mesmo relatório como extrato genérico.

Os resumos financeiros contam apenas `cash_effect`. Emissão de documento fiscal não compõe entradas ou saídas de caixa. Nenhum dos três relatórios informa o saldo da conta corrente; saldo inicial/final bancário permanece desconhecido.

As interfaces HTML e PRO apresentam eventos com sua natureza, situação e origem. A edição de linhas desses novos relatórios fica indisponível nesta versão para manter a associação entre campos detalhados, valores e validações. A edição de extratos anteriores permanece disponível.

Quando um relatório reconhecido aparece como anexo de um extrato, suas páginas são identificadas e separadas dos lançamentos bancários pelo mecanismo existente. A extração do relatório deve ser feita com o documento separado. Um arquivo com relatórios especializados de tipos diferentes recebe orientação para envio separado.

## Exportação

- **CSV:** UTF-8 com BOM, colunas estáveis e campos específicos preservados. `record_type=event` identifica eventos; `record_type=observation` identifica posições. Campos ausentes ficam vazios.
- **XLSX:** abas Eventos, Detalhes, Posições, Validações e Documento. Valores monetários são células numéricas; identificadores permanecem texto para preservar zeros à esquerda. Também é o relatório retornado por `/report/{analysis_id}`.
- **OFX de recebimentos:** utiliza a data de pagamento e o valor cobrado. Exige banco, agência e conta de destino explícitos; não inclui saldo bancário. Não afirma a data de crédito ou o líquido efetivamente creditado pelo banco. Investimentos e registros fiscais não são projetados como OFX bancário.

A política é aplicada no download e na geração do artefato, inclusive após recarga pelo S3. Parâmetros de conta ou saldo não habilitam OFX para tipos incompatíveis. A exportação ZIP de lotes inclui XLSX para esses relatórios e mantém OFX para as conversões bancárias anteriores.

## Evolução e implantação

Esta entrega não implementa conciliação entre documentos, clientes ou períodos. Essa etapa pertence ao OFX Simples PRO. Os eventos preservados fornecem referências para essa evolução sem somar novamente observações ou documentos fiscais como receitas.

Não há migração de banco nem alteração de retenção. Os campos novos são gravados nos JSONs privados e temporários já existentes. O histórico operacional aceita os novos tipos; nenhuma amostra de cliente faz parte dos testes versionados.

Publique primeiro a API compatível com o contrato e as restrições de exportação; depois o worker da mesma versão e as interfaces. Workers anteriores continuam produzindo resultados bancários legíveis pela nova API. Um worker novo não deve publicar esses relatórios para uma API anterior, que desconhece sua natureza.

O rollback deve preservar a API que aplica as restrições enquanto existirem resultados `financial_document` dentro do prazo de retenção. Interrompa novos processamentos durante uma reversão do worker; não encaminhe esses relatórios ao parser bancário anterior como alternativa, pois essa era a origem dos lançamentos incorretos. Uma reversão integral exige primeiro expirar ou invalidar os resultados especializados, ou manter as guardas de exportação em um hotfix. Os JSONs e resultados bancários anteriores não precisam de migração.

Validação: testes sintéticos de extração/contrato/artefatos, recarga S3, HTTP real com worker, interfaces HTML/PRO e comparação privada dos 27 casos históricos. O endpoint antigo `/analyze` está desabilitado; os caminhos atuais de validação são `/convert` e os lotes assíncronos.
