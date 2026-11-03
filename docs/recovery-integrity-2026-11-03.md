# Integridade dos casos de 2 e 3 de novembro de 2026

A conferência local dos 12 registros preservados do bucket de recuperação identificou dez PDFs distintos. Todos tinham texto nativo; nenhum precisou de uma nova chamada de OCR. Os originais permanecem fora do Git. As regressões versionadas usam documentos sintéticos.

## Por que um layout Sicredi conhecido falhou

O reconhecimento do layout estava correto, mas não garantia a cobertura de todas as suas representações. O teste anterior do modelo moderno exercitava três movimentos que repetiam a data explicitamente na própria linha.

O extrato de setembro imprimia a data somente no primeiro movimento de cada grupo, com continuação entre páginas e descrições quebradas. O leitor exigia data e valor juntos e ignorava as linhas seguintes sem data: extraiu 14 de 45 movimentos. Além disso, a ordem dos objetos de texto no PDF colocava parte do rodapé antes de movimentos na última página. A leitura espacial nativa recupera a ordem visual sem OCR.

O extrato de agosto apresentava data, descrição e valor em linhas distintas na extração linear. A assinatura do layout era reconhecida, mas a ausência de linhas completas fazia o processamento cair no leitor genérico. Esse leitor importava saldo atual, bloqueios, limites e percentuais junto dos 54 movimentos reais, produzindo 61 entradas.

A correção usa uma política compartilhada de seção de movimentos, data herdada, reconstrução de descrições e controles impressos. O leitor de layout e o caminho de extratos verificados usam a mesma implementação. Juros e IOF efetivamente lançados continuam sendo movimentos; a seção de limites e taxas é separada por sua estrutura. As referências de página e linha incluem a origem da data herdada.

Os dois Sicredi de referência que já funcionavam também foram reprocessados: C14, matricial, manteve 96 movimentos e líquido −4.964,91; C21, com totais diários em HTML, manteve oito movimentos e líquido −79,94. Aberturas, fechamentos e ausência de falhas de saldo permaneceram iguais. Portanto, o problema era a cobertura das representações do modelo moderno, não a inexistência de suporte ao banco.

Controles diários e fechamento são conferidos em centavos, inclusive quando os movimentos estão em ordem decrescente. Valores de saldo ilegíveis não podem consumir o lançamento seguinte: os movimentos legíveis permanecem e exigem revisão. Datas ou valores de transações realmente ilegíveis continuam sendo ambiguidades de extração. Não há ajuste de valores pela diferença do saldo nem remoção de repetições legítimas.

## Resultado nos documentos coletados

| Documento | Antes | Depois |
| --- | --- | --- |
| Sicredi, setembro | 14 movimentos, 31 omitidos | 45 movimentos: 14 na página 1, 26 na 2 e cinco na 3; abertura 22,31 e fechamento 1.218,13 conferem |
| Sicredi, agosto | 61 entradas, sete sem natureza transacional | 54 movimentos; abertura −15,57 e fechamento 22,31 conferem, incluindo os controles diários |
| Sicoob SISBR Saldo Renda Fixa, três PDFs | Oito posições convertidas em movimentos na data de emissão | Oito observações por PDF, com a data de posição original: 30/09, 01/10 ou 29/10; CSV/Excel preservam os campos e OFX permanece indisponível |
| Caixa, agosto | Abertura lida na coluna de movimento: zero | Abertura 6.005,01 na coluna de saldo; 87 movimentos preservados; fechamento 16.416,68 |
| Itaú, fevereiro e março | Fechamento calculado sem controle bancário comparável | 279 e 135 movimentos preservados; abertura/fechamento indisponíveis e revisão explícita; OFX não cria saldo bancário |
| Viacredi/Ailos | Revisão por ausência de maio | 799 movimentos preservados; mantém a revisão de continuidade e não valida um saldo global |
| PagBank | Abertura inferida a partir do fechamento | 34 movimentos preservados; mantém o aviso de abertura inferida |

A comparação dos Sicredi inclui um multiconjunto independente de data e valor com sinal, extraído da tabela espacial, preservando multiplicidades. Nos demais extratos bancários, a comparação confirma as mesmas datas, valores e repetições dos resultados anteriores. Dois registros repetem o mesmo Itaú e dois repetem o mesmo PagBank; não representam movimentos duplicados dentro do arquivo.

## Políticas compartilhadas e compatibilidade

- Cabeçalhos `Movimentações de … a …` participam da identificação de blocos mensais. Períodos independentes são conciliados separadamente e recombinados com proveniência física; diferenças entre fechamento e abertura continuam exigindo revisão.
- Em tabelas com colunas declaradas de valor e saldo, a abertura vem da coluna de saldo, com seu sinal. Em sequências tabulares comprovadamente crescentes por data, esse controle também confere o primeiro movimento.
- Posições Sicoob usam o contrato existente `financial_events_v1`, com observações e datas de posição, sem movimentos fictícios. Valor disponível para resgate não é forçado a coincidir com o líquido: carência e bloqueios têm significado próprio.
- Controles que combinam caixa e investimentos não fornecem limites de saldo de conta corrente. O marcador existente `balance_bounds_unavailable` impede que preview, armazenamento e exportação reconstruam um saldo fictício.
- A interface também respeita esse marcador: mostra “Não informado”, não calcula saldos intermediários ausentes e não envia zero ou o líquido dos movimentos como saldo no download, mesmo ao restaurar uma tela antiga. Saldos explicitamente impressos, inclusive zero, continuam preservados.

Não há alteração de schema persistente, migration ou contrato público removido. API e worker devem receber o mesmo código. Resultados já armazenados permanecem inalterados; a correção vale para novas conversões ou reprocessamento explicitamente solicitado. Nenhum documento foi reenviado ou reprocessado em produção nesta atividade.

## Validação e isolamento da otimização de testes

A branch de correção parte de `main` (`2149989`) e não contém a otimização do PR #540. A suíte completa usa um checkout local separado, baseado em `c3976cc`, com uma cópia das alterações do produto. Assim é possível aproveitar a infraestrutura otimizada sem fazer merge dela nem tornar o PR do produto dependente daquele PR.

Os testes cobrem texto nativo e OCR textual, datas herdadas entre páginas, campos fragmentados, descrições continuadas, débitos de juros/IOF, repetições legítimas, sinais, controles ilegíveis, múltiplos períodos, continuidade, abertura na coluna correta e datas de posição. Os fluxos HTTP reais cobrem upload, worker, downloads CSV/XLSX/OFX, relatórios existentes e IDs inexistentes.

A suíte completa final do backend passou: **4.066 testes aprovados, 55 ignorados e duas falhas esperadas**, sem falhas inesperadas. O JUnit registrou 481,138 segundos e o processo completo 484,366 segundos, com um único processo de teste e 843 MiB de memória disponível no início. Uma execução anterior levou 294,01 segundos; esses tempos locais variam com a disponibilidade da máquina e não constituem uma garantia de duração. Os testes opcionais ignorados e as falhas esperadas já existiam.

Após a suíte completa, a correção da interface foi validada executando suas funções JavaScript reais em Node: conversão, edição, exibição, restauração de valores antigos e envio das configurações de download. O teste reproduziu o saldo zero incorreto antes da alteração e passou depois. O conjunto de regressões da interface passou com 14 testes, além de Ruff, sintaxe JavaScript e lints de texto UTF-8/mojibake e navegação.

A validação final combinada na branch independente, sem a otimização, passou com **69 testes e dois avisos de depreciação existentes em 20,84 segundos**. Esse conjunto inclui todos os novos testes, os exemplos conhecidos de Sicredi e as regressões relacionadas da interface.

Rollback: reverter o commit de correção e republicar API e worker. Não há dados a migrar ou transformar. Os PDFs originais e os resultados antigos continuam disponíveis conforme suas políticas de retenção habituais.
