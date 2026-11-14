# Auditoria de recuperação — 14/11/2026

## Escopo e evidência

Snapshot somente de leitura do bucket de recuperação de produção, encerrado em
14/11/2026 às 10:55:05 de Brasília (13:55:05 UTC). A listagem continha 1.544 objetos,
385 manifestos e 300 hashes de documento distintos. Dois conjuntos estavam
incompletos; os outros 298 documentos foram baixados e conferidos por SHA-256.
Os originais, diagnósticos e a planilha por captura permanecem em diretório
temporário privado, fora do repositório e do OneDrive.

| Resultado original | Capturas | Documentos com replay |
| --- | ---: | ---: |
| Falha/recusa | 94 | 48 |
| Conversão com revisão | 291 | 250 |

Os dois conjuntos incompletos estão entre as capturas de revisão. Estes números
descrevem o material retido pelo fluxo de recuperação, não uma taxa de falha de
todas as conversões de produção. Capturas repetidas não são usuários distintos.
O replay usa o parser nativo ou a evidência OCR armazenada; não invoca Textract
ou Bedrock nem modifica análises de produção.

## Recusas corrigidas

Quatro documentos, presentes em 15 capturas, passam a produzir 675 movimentos.
Uma leitura independente da disposição textual dos PDFs conferiu o multiconjunto
de datas e valores assinados de cada documento: 1, 4, 339 e 331 movimentos,
respectivamente, sem perdas ou acréscimos. As 15 capturas foram reexecutadas.

| Caso | Defeito | Resultado |
| --- | --- | --- |
| PagBank: 1 documento, 2 capturas | Exigia pelo menos um intervalo entre dois fechamentos, recusando um extrato com apenas um dia movimentado. | Preserva o movimento e o fechamento. Mantém `opening_inferred_from_closing`, zero verificações independentes e revisão obrigatória. |
| Sicoob: 1 documento, 8 capturas | Tratava saldo de bloqueio judicial anterior como abertura disponível e tentava resolver o ano de um controle anterior dentro do mês dos movimentos. | Mantém separados saldos bloqueados e disponíveis. Compara o controle da mesma data textual da abertura, antes dos movimentos, sem inventar um ano. Os quatro movimentos e controles conferem. |
| Itaú: 2 documentos, 5 capturas | Recusava tabelas integralmente legíveis quando faltava a seção final de resumo. | Extrai 339 e 331 movimentos, com origem por página e linha, mantendo cobertura não confirmada e `partial_statement`. Não declara validação global. |

O controle adicional Sicoob só é tratado como observação da abertura quando
precede qualquer movimento, tem a mesma data textual e está identificado como
`SALDO DO DIA`. Uma divergência conserva os movimentos e exige revisão. Datas
abreviadas dos movimentos continuam exigindo contexto inequívoco de ano.

A ausência do resumo Itaú não autoriza omitir linhas: valores/datas ilegíveis,
continuações sem associação e outras páginas após um encerramento reconhecido
continuam protegidos. Blocos mensais repetidos preservam todos os movimentos,
inclusive repetições legítimas, e mantêm revisão quando a cobertura é incerta.

## Revisões e diferenças anteriores

Aplicando a orientação atual aos campos disponíveis nas 289 capturas completas
de revisão:

| Orientação | Capturas |
| --- | ---: |
| Informação, sem recomendação de revisão por esse critério | 129 |
| Divergência financeira sinalizada | 45 |
| Validação incompleta | 115 |

As 129 capturas informativas já são tratadas pela regra existente; este lote não
altera essa política. O manifesto `manual_review_recommended`, isoladamente,
não comprova que a interface tenha exigido revisão indevida. Esta reconstrução
também não substitui campos de interface que não estejam na captura.

Os 250 documentos de revisão puderam ser lidos novamente. Em 240, o multiconjunto
data/valor coincide com a captura. Dez diferem; o replay dos mesmos dez na
`origin/main` (`09d0f7d`) produz exatamente os mesmos movimentos que a branch.
Portanto essas diferenças são anteriores a esta mudança e não foram aprovadas
como correções nesta auditoria. Nove replays resultam em `safe_to_export` no
código atual, sem alteração adicional da política de revisão neste lote.

## Pendências identificadas

Após as correções permanecem 79 capturas de falha, correspondentes a 44 documentos.
Não são classificadas em bloco como recusas justificadas ou indevidas. A planilha
privada registra o erro reproduzido e o manifesto de cada captura. Prioridades:

- Fundos BB: três documentos esbarram em cabeçalho de impressão e associação das
  colunas/posições. Exigem verificar também operações e subtotais do investimento.
- SICREDINVEST: dois documentos usam relatório simplificado com movimentos
  datados, diferente da matriz mensal de oito campos aceita pelo leitor atual.
- C6: documento com continuação sem cabeçalho e datas contábeis atravessando o
  período; exige resolver cada bloco sem descartar movimentos anteriores.
- Sicoob/Sicredi e outros leitores: associação de datas, sinais, valores, saldos
  sem data repetida, referências e continuidade entre páginas ainda precisa ser
  confrontada com o original. Erro do parser, por si só, não prova ilegibilidade.
- Itaú com conteúdo após o resumo: há uma tabela adicional de lançamentos futuros;
  não pode ser descartada genericamente nem misturada aos movimentos realizados.
- Três documentos continuam classificados como notas fiscais não suportadas.
  Outros arquivos têm camada textual suspeita ou estrutura ainda não reconhecida.

Os dois conjuntos incompletos não podem ser auditados integralmente a partir
desse snapshot. As dez diferenças de replay anteriores também permanecem
sinalizadas para conferência, sem redução dos alertas financeiros.

## Validação e compatibilidade

- Etapa vermelha: 8 falhas e 4 sucessos nas regressões novas.
- Leitores relacionados: 105 testes aprovados após as correções.
- Suíte completa pelo runner: **4.966 aprovados, 89 ignorados e 2 falhas esperadas**,
  em 594 segundos. Inclui os 15 cenários novos. Os dois avisos são depreciações
  existentes do FastAPI; o diretório temporário do runner foi limpo ao concluir.
- HTTP real cobre `/convert`, `/report/{analysis_id}`, CSV/Excel/OFX, recusas de
  dados danificados e relatório inexistente. `/analyze` permanece desativado
  com HTTP 405, como na API vigente.
- Gate PDF contra `origin/main`: 90 cenários, nenhuma diferença inesperada,
  nenhuma aprovação adicionada.
- Gate de revisão/armazenamento: 170 cenários, nenhuma diferença inesperada.
- Ruff de `backend/app` e `backend/tests`, lint de texto UTF-8/mojibake e
  navegação aprovados.

## Produção e rollback

Sem mudança de schema, dependências, contratos HTTP, armazenamento ou infraestrutura.
Nenhuma gravação no S3, reprocessamento em produção, merge ou deploy. Resultados
antigos armazenados permanecem intactos; os leitores corrigidos são aplicados a
novas conversões após implantação.

O risco principal é uma variante não coberta das linhas de controle ou do fim
da tabela. As verificações de ambiguidade permanecem e a falta de fechamento
de cobertura é explicitada. Rollback: reverter o commit e publicar a imagem
anterior; não há migração de banco a desfazer.
