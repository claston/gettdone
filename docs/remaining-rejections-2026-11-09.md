# Rejeições remanescentes da auditoria de 09/11/2026

## Escopo e resultado

A auditoria privada reúne 279 capturas de recuperação e 40 PDFs distintos que
haviam falhado. A atualização do inventário às 19:24 BRT de 09/11 não encontrou
novas capturas de falha. Esses números descrevem a amostra do bucket, não uma
taxa de rejeição de todo o tráfego de produção.

Dos 40 casos, três já eram interpretados pelo `origin/main` usado como base
(`a1de03d`), quatro pertencem ao PR #569 e 33 foram examinados nesta alteração.
O replay local recupera **23 dos 33**; dez continuam sem extração segura.
Os arquivos originais, textos extraídos e dados pessoais permanecem fora do Git.
Os testes adicionados usam exclusivamente documentos fictícios.

| Família | Casos recuperados | Correção |
| --- | ---: | --- |
| Banco do Brasil | 5 | Documento opcional, geometria da poupança, extrato comprovadamente sem movimento e página final vazia com pequena marca de scanner na borda. |
| Itaú | 3 | Capa antes da tabela, controles de aplicação automática e poupança separados dos movimentos. |
| Wise | 2 | Metadados de cartão, última página exclusivamente legal e divergência de saldo encaminhada à revisão. |
| Cora | 1 | Grupos diários decrescentes com valor assinado antes do histórico e conferência dos totais. |
| Sicredi | 2 | Cabeçalho abaixo da posição usual e tabela matricial comprovadamente sem movimento. |
| Sicoob | 4 | Abertura dentro do período, saldo judicial como controle, resumo na página seguinte e conta sem movimento. |
| Bradesco | 2 | Blocos independentes em “Últimos lançamentos”, inclusive iniciados na mesma página física. |
| Santander | 1 | Encerramento da tabela antes da composição Contamax. |
| C6 | 1 | Totais mensais compactos e datas contábil/lançamento na mesma linha. |
| BTG | 1 | Metadados fragmentados e abertura indicada por traço, preservada como desconhecida. |
| PagBank | 1 | PDF nativo encaminhado ao contrato financeiro já usado no OCR. |

Dez dos casos recuperados retornam `safe_to_export`; nove retornam
`review_recommended`; três são extratos sem movimento comprovados. O caso
PagBank usa validações do documento financeiro: disponível e recebíveis são
subcontas diferentes, com CSV/Excel e sem OFX bancário misturando essas posições.
Esses indicadores não substituem a conferência humana do documento original.

## Limites preservados

- Divergências aritméticas e cobertura não confirmada geram revisão explícita.
  Não há ajuste de valores nem criação de movimentos para fechar saldos.
- O traço na abertura BTG não vira zero. Os movimentos legíveis permanecem;
  não é declarado um saldo global validado.
- Blocos Bradesco mantêm suas aberturas, movimentos, repetições legítimas e
  páginas físicas. A combinação passa pela política existente de continuidade.
- Apenas ausência de movimentos não comprova inatividade. Exige-se evidência
  positiva de tabela vazia; valores extras e imagens na tabela impedem esse caminho.
- A exceção de página vazia aceita somente poucos pixels claros na borda
  externa. Marcas internas, escuras ou maiores continuam exigindo leitura.

## Dez casos pendentes

| Quantidade | Evidência e próxima necessidade |
| ---: | --- |
| 3 | BB com valores sem C/D no OCR armazenado. É necessária evidência inequívoca da direção de cada lançamento; o saldo não fornece um sinal substituto. |
| 1 | Cresol com valor sem indicador C/D; “cartão débito” no histórico não determina uma saída da conta. |
| 1 | BTG com ordem espacial diferente da ordem textual e sinais ausentes no OCR. Requer interpretação geométrica e evidência dos sinais. |
| 1 | BB com tabela assinada e cabeçalhos/rodapé que exigem tratamento adicional da associação entre datas e valores. |
| 1 | Sicredi parcial com saldo “de transporte” e células agrupadas pelo OCR. Requer tratamento geométrico de continuidade. |
| 1 | Bradesco OCR com novo bloco na terceira página e colunas deslocadas. Requer separação de blocos também nessa representação. |
| 1 | Bradesco em imagem, visualmente legível, sem evidência OCR armazenada. Precisa de uma nova extração OCR para ser avaliado. |
| 1 | Balanço patrimonial SPED: posições contábeis, não movimentos bancários. Precisa de contrato específico antes de exportar esses dados. |

Não houve reprocessamento em produção nem alteração no bucket.

## Validação e entrega

Regressões sintéticas cobrem referências opcionais, datas e sinais, blocos e
páginas, controles incompletos, valores ilegíveis, inatividade e limites da
página vazia. O teste HTTP real cobre CSV e PDFs recuperados, `/convert`,
`/report`, CSV/Excel/OFX, revisão persistida e relatório inexistente. O endpoint
legado `/analyze` retorna 405, conforme o contrato atual.

Os gates contra `origin/main` aprovaram 90 cenários de PDF e 170 cenários de
revisão/armazenamento. A combinação com o PR #569 aplicou sem conflito, passou
122 testes e interpretou 30 dos 40 documentos da amostra (23 desta alteração,
quatro da entrega anterior e três já suportados na base). A suíte completa é
registrada no PR de entrega. Não há mudança de schema, dependências ou API.
Rollback: reverter o commit da correção; relatórios já armazenados mantêm o
contrato existente. Os arquivos originais podem ser reenviados após implantação.
