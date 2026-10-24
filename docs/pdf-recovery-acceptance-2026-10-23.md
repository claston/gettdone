# Recovery e proteção contra regressões de conversão

A aplicação deve converter movimentos legíveis e indicar inconsistências para
revisão. Divergência de saldo, período ou paginação não deve, por si só, impedir
a conversão. Datas e valores sem associação legível continuam exigindo outra
cópia; nenhum valor é calculado a partir da diferença dos saldos.

## Casos corrigidos

| Documento | Causa e comportamento atual | Validação local com o arquivo capturado |
| --- | --- | --- |
| Bradesco mensal | Texto extraído com letras separadas e descrições em várias linhas. A leitura usa as colunas nativas, continua entre páginas e separa o apêndice “Últimos Lançamentos”, de outro período. | 154 movimentos, 154 saldos conferidos, nenhuma divergência aritmética. |
| BB | O período do cabeçalho não abrangia todos os movimentos históricos impressos. Preservar suas datas e valores e avisar sobre o período. | 8 movimentos, 5 controles de saldo sem divergência, revisão recomendada. |
| Sicredi, dois PDFs digitalizados | OCR juntava data e descrição ou valor e saldo, com inclinação nas páginas. Associar células pela geometria original, preservar movimentos repetidos e excluir lançamentos futuros. Funciona nos modos OCR de texto e tabelas. | 111 e 150 movimentos; 6 e 3 divergências de saldo permanecem sinalizadas. |
| XP Conta Investimento | O relatório era confundido com conta corrente Sisprime. Identificar XP e manter movimentação, liquidação, valores, saldos, posições e projeções em suas respectivas categorias. | 152 eventos, nenhuma divergência nos controles financeiros. Excel/CSV; OFX indisponível para esse relatório. |

No Sicredi, um mês `00` do OCR só pode usar o único mês explicitamente impresso
no período, com datas vizinhas compatíveis e dia válido. Isso gera aviso de
revisão. Datas com dia inválido ou período ambíguo continuam sendo recusadas.
Uma anotação junto de um valor com sinal explícito não permite inferir outro
sinal ou dígito. Valores de movimentos identificados como manuscritos são
recusados. Saldos de controle divergentes geram avisos e preservam os movimentos.

Os arquivos privados e o OCR salvo permanecem fora do Git. A amostra anterior
de 58 capturas mantém as mesmas 49 conversões e quantidades; os demais casos
continuam sem sucesso. Não foi chamado OCR remoto nem Bedrock nesses replays.
Outras 16 capturas posteriores também convertem, com as mesmas quantidades da
`main` usada como base, incluindo os relatórios genéricos de contas a pagar.

## Gates

- A suíte inclui aceitação de produto: movimentos repetidos, datas, sinais,
  valores, identidade do documento, proveniência, avisos e formatos permitidos.
  Há upload HTTP, consulta de relatório, downloads CSV/Excel/OFX e execução do
  worker. Incompletude e inconsistências continuam cobertas em outros layouts.
- `scripts/check_pdf_compatibility.py --base-ref origin/main` cria 13 casos
  fictícios, executa o pipeline da base e o candidato em processos separados e
  compara resultados com os movimentos esperados da fonte. Rejeição de entrada
  legível, perda de repetição, alteração de data/sinal/valor ou identidade e
  desaparecimento de aviso obrigatório bloqueiam o gate. Mudar um resultado
  antes aceito exige justificativa explícita e conformidade com a fonte.
  Isso inclui o Bradesco em texto sequencial com saldo próprio na seção seguinte,
  que continua usando sua leitura existente e preservando ambas as seções.
- O comparador integra `lint-and-tests`, junto da suíte completa. No PR a base é
  o SHA de destino; no push é o commit anterior. A ausência de uma base válida
  causa falha. Os relatórios fictícios ficam no artefato `pdf-compatibility-*`.
  A extração temporária da base usa `git archive`, sem criar worktree.
- O deploy manual do worker exige CI `push/main` concluído com sucesso para o
  SHA exato da publicação, incluindo `lint-and-tests`, `pdf-golden` e
  `pro-quality`. Jobs ausentes, pulados ou em execução impedem solicitar as
  credenciais AWS. É preciso aguardar o CI após o merge antes de implantar.

Esses contratos ampliam a proteção dos casos conhecidos; novos layouts ainda
precisam de exemplos próprios para ter sua semântica verificada pelo CI.

## Métricas e compatibilidade

Nas novas conversões PDF, confiança de leitura baixa ou média gera qualidade
`review` com `parser_requires_review`, mesmo se o layout tiver alta confiança.
Isso evita exibir “limpa” quando o parser recomenda revisão. A regra de qualidade
é `2026-10-23.v2` e o classificador de documentos é `2026-10-23.4`.

Não há migração, mudança de esquema, reescrita dos históricos ou alteração de
objetos existentes no S3. Não há reprocessamento automático das capturas. A
representação financeira e as rotas de download existentes são mantidas.

O risco principal é associar incorretamente células em variantes de OCR ainda
não observadas. As proteções recusam células ambíguas e preservam avisos de
saldo/período; o gate compara o comportamento com a versão base. O rollback é
reimplantar a imagem anterior e reverter o PR, sem operação de banco de dados.
Os novos bloqueios de CI/deploy podem ser revertidos separadamente se houver
indisponibilidade do GitHub Actions; a política normal exige reparar o CI.
