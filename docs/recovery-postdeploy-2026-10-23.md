# Ajustes da auditoria do recovery de 23/10/2026

As correções usam PDFs e evidências de OCR já capturados. Os arquivos reais
permanecem fora do Git; as regressões versionadas usam dados fictícios.

| Caso | Tratamento |
| --- | --- |
| Revisão recomendada sem código de erro | Capturar no recovery com `manual_review_recommended`, inclusive em arquivos acima de 15 páginas. |
| Santander com identificação em colunas | Associar agência e conta pelos rótulos; manter a proteção contra contas diferentes. |
| Santander digitalizado | Excluir contador de página das células monetárias, reconhecer tarifas, juros e pagamentos e tolerar o deslocamento vertical dos saldos. Valores legíveis com divergências continuam com aviso. |
| Cresol com vários meses | Separar blocos por período da mesma conta e preservar datas, saldos e referências às páginas originais. |
| Mercado Pago | Preservar retenções, liberações e demais eventos com o mesmo ID; avisar `shared_operation_reference` quando a relação não for o par conhecido de Pix enviado/reembolso integral. |
| BB digitalizado | Associar Dia/Lote/Documento/Histórico/Valor por coordenadas e sinais explícitos. Completar somente o último dígito truncado do ano quando todas as datas completas informarem um único ano compatível; avisar para revisão. |
| BB com marca d’água | Ler os glifos horizontais e aceitar saldo anterior impresso na data inicial do período. |
| Banpará | Separar valor e saldo por coordenadas, incluindo movimentos com data e descrição em células distintas. Recusar valor sem data legível associada. |
| Bradesco | Preservar pagamentos cuja descrição inclui “BRADESCO NET EMPRESA”; o nome do banco isoladamente não identifica ruído. |
| Contas a pagar / receber | Identificar o relatório e mapear colunas por nomes e geometria, sem perfil por ERP. Exportar títulos e campos originais para Excel/CSV, sem efeito de caixa ou OFX. |

Inconsistências aritméticas e sinais de arquivo incompleto geram revisão sem
alterar os valores legíveis. Datas, valores ou sinais sem associação confiável
continuam exigindo outra cópia. A captura mantém o Bedrock desativado.

Não há migração, alteração dos objetos existentes no S3 ou reprocessamento
automático. A ampliação da captura pode aumentar o volume temporário do
recovery; a retenção continua sendo a configurada no bucket. Reverter a
versão restaura os leitores anteriores; `AI_RECOVERY_MODE=off` desliga a captura.
