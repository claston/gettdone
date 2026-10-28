# Correções complementares dos extratos auditados em 28/10/2026

Esta entrega reúne as falhas restantes em uma única PR, complementar às
correções de período opcional (#501) e múltiplos meses (#502). O escopo é o
lote auditado de 12 PDFs distintos; não é uma nova consulta de falhas em produção.

## Comportamento corrigido

- **Mercado Pago:** separa histórico e referência quando o agrupamento de texto
  nativo produz uma única linha para as duas colunas. Usa as palavras e suas
  coordenadas originais e mantém a conferência contra os valores, datas,
  referências e saldos da outra extração nativa. Referências compartilhadas não
  eliminam movimentos.
- **BB:** reconhece depósitos explicitamente bloqueados com marcador `*` como
  pendências sem efeito no caixa disponível. Mantém o aviso de exclusão; um
  asterisco num movimento desconhecido não basta para descartá-lo.
- **BB:** aceita um saldo do dia anterior ao início do período como controle
  de abertura impresso. Históricos deslocados acima da data/valor conservam
  suas referências de origem. Se apenas a associação do histórico falhar na
  leitura horizontal, tenta a extração que preserva o layout, exigindo a mesma
  quantidade e identidade de datas, valores e pares valor/sinal nas duas
  visões. Depois, executa novamente o leitor e seus controles financeiros.
- **Sicoob SISBR:** recupera cabeçalhos e células agrupadas a partir das palavras
  originais. Processa a ordem crescente ou decrescente e confere saldos diários,
  abertura e fechamento após ordenar os movimentos. Nenhum valor ou sinal é
  preenchido a partir da diferença de saldo.
- **Blocos:** um saldo de abertura de investimento localizado depois de
  movimentos de caixa na mesma página não inicia outro extrato. Isso corrige
  a regressão de separação que afetava o Bradesco de 10 páginas.

## Resultado do lote local

| Caso | Documento | Páginas | Transações | Resultado / ação sugerida |
| --- | --- | ---: | ---: | --- |
| D28-01 | Mercado Pago | 23 | 295 | Converteu; conferir ocorrências com referência compartilhada. |
| D28-02 | Bradesco | 10 | 403 | Converteu; confirmar a cobertura do período no original. |
| D28-03 | BB | 3 | 53 | Converteu; um depósito bloqueado ficou fora do caixa disponível. |
| D28-04 | BB | 4 | 77 | Converteu; um depósito bloqueado ficou fora do caixa disponível. |
| D28-05 | Mercado Pago | 24 | 275 | Converteu; conferir ocorrências com referência compartilhada. |
| D28-06 | Sicoob SISBR | 12 | 136 | Converteu; controles automatizados de saldo conferiram. |
| D28-07 | BB | 8 | 212 | Converteu; a leitura alternativa preservou os campos financeiros. |
| D28-08 | BB, 12 meses | 72 | 1.618 | Converteu; mantido o resultado da correção anterior. |
| D28-09 | Sicoob SISBR | 17 | 222 | Converteu; controles automatizados de saldo conferiram. |
| D28-10 | Sicoob SISBR | 15 | 203 | Converteu; controles automatizados de saldo conferiram. |
| D28-11 | Mercado Pago | 2 | 12 | Converteu; controles automatizados de saldo conferiram. |
| D28-12 | Mercado Pago | 30 | 370 | Converteu; conferir ocorrências com referência compartilhada. |

São **3.876 transações**, com exportações CSV, XLSX e OFX contendo todos os
movimentos de cada resultado e sem divergências de saldo detectadas pelos
controles executados. Nos 11 PDFs BB/Mercado Pago/Sicoob, a comparação adicional
com os registros da camada de texto conservou datas, sinais, valores e
multiplicidade, sem movimentos faltantes ou adicionais. O Bradesco conservou
403 movimentos, com os saldos e totais impressos conferidos pelo leitor.

Os arquivos, dados e exportações reais permanecem em diretório local ignorado;
os testes versionados usam exclusivamente dados fictícios. Os avisos de
revisão são preservados, não uma certificação contábil do resultado.

## Compatibilidade e implantação

Não há migração, mudança de estrutura de banco, backfill ou reclassificação
retroativa. A contagem de depósitos pendentes usa as métricas existentes. A
propriedade que permite tentar outra visão nativa é interna à exceção de
leitura, sem mudança no contrato público.

Implantar esta correção com as anteriores no backend/worker. Após a implantação,
reenviar os PDFs que falharam: a entrega não reprocessa registros históricos
automaticamente. Conferir a quantidade dos downloads e os motivos de revisão
no Admin. Para rollback, restaurar a versão anterior do backend/worker; não há
rollback de esquema.
