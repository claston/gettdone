# Movimentos legíveis e controles opcionais

O quarto lote confirma e corrige três recusas que estavam inconclusivas na
auditoria de 10/11. A conferência do original distingue ausência de controle,
ordenação dos dias e referências opcionais em linhas de saldo.

| Caso | Causa confirmada | Comportamento |
| --- | --- | --- |
| Sicoob, duas páginas | Ausência de saldo inicial bloqueava movimentos com valores e sinais explícitos. | Preserva 19 movimentos, documentos e complemento de histórico entre páginas; confere somente os intervalos posteriores a um saldo diário impresso. |
| Extrato por período, uma página | Leitor assumia dias em ordem decrescente e usava o saldo inicial como limite final da página; o original está em ordem crescente. | Preserva seis movimentos e confere os três saldos diários; abertura zero e fechamento de R$ 237,60. |
| BB com conta corrente e Rende Fácil, 16 páginas | Uma linha de saldo combinado sem documento/lote era recusada pela expressão que exige referência de movimento. | Preserva 360 movimentos e suas referências; documento/lote são opcionais somente na linha reconhecida de saldo combinado. |

## Limites financeiros

O Sicoob não recebe saldo inicial calculado por diferença. O primeiro saldo
diário disponível permite comparar os movimentos seguintes com o próximo
controle, sem validar movimentos anteriores a esse primeiro saldo. As 13
comparações do original apresentam divergências e mantêm revisão explícita.
Isso não permite concluir que os valores dos movimentos estejam errados nem
inventar transações para fechar a conta. Saldos diários impressos permanecem
ligados ao último movimento da respectiva data.

O saldo BB agrega conta corrente e investimento. Ele continua fora do cálculo
do saldo bancário exportado, com a orientação de revisão existente. A dispensa
de documento/lote nessa linha não relaxa campos de movimentos: datas, valores,
sinais e referências de transações continuam sujeitos à leitura completa.

No extrato por período, os saldos estavam presentes. A correção trata a ordem
dos dias e o limite da página; não passa a aceitar um saldo ilegível nem um
dia sem controle. Datas misturadas, etiquetas incompatíveis e valores sem
associação continuam recusados. A mesma leitura geométrica atende PDF nativo
e evidência OCR.

## Evidência e regressões

- Comparação independente dos 385 movimentos com os originais: datas, valores,
  sinais, multiplicidade, referências disponíveis e páginas coincidem.
- BB e Sicoob foram confrontados com extração de layout independente via
  pypdf; os seis movimentos do extrato por período foram conferidos na imagem.
- Sicoob e extrato por período também passaram no replay com geometria nativa
  apresentada à rota OCR. Isso é uma simulação de representação, sem chamada
  remota nem promessa de qualidade de um novo OCR.
- Testes fictícios cobrem abertura ausente, controles divergentes, débitos com
  sinal ilegível, ordem crescente/decrescente, dias e valores órfãos, múltiplos
  meses, paginação reiniciada, referências e históricos que atravessam páginas.
- HTTP real cobre conversão dos três modelos e CSV, consulta do relatório,
  downloads CSV/Excel/OFX e relatório inexistente. Sicoob e BB não recebem saldo
  inicial inventado nem `LEDGERBAL` no OFX. A rota pública atual é `/convert`;
  `/analyze` permanece desativada, respondendo 405.

## Compatibilidade e implantação

Não há migração, alteração de endpoint, dependência, interface ou configuração
de produção. O contrato de transações, evidência de origem e orientação de
revisão permanece o existente. O processamento novo pode preencher referências
Sicoob antes omitidas; movimentos repetidos permanecem separados.

Os originais e diagnósticos continuam na pasta local privada, fora do commit.
Este lote não reprocessa clientes nem faz deploy. Após eventual publicação,
acompanhar recusas desses leitores, avisos financeiros e saldos exportados.
Rollback: reverter o commit e publicar a versão anterior; sem migração a desfazer.
