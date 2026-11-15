# Identificação do titular: auditoria de cabeçalhos de novembro

## Problema e correção

A auditoria local encontrou nomes e CNPJs legíveis que não eram recuperados quando o PDF invertia a ordem textual dos rótulos, distribuía os campos em colunas ou intercalava o logotipo no OCR. Também encontrou um CNPJ de fundo de investimento como candidato a documento do cliente e o rótulo “Cliente Corrente” como candidato a nome.

O reconhecedor compartilhado, versão 7, passa a:

- Associar colunas explicitamente rotuladas a seus valores, preservando as referências de página e linha.
- Recuperar CNPJ na linha seguinte ao nome, espaços junto à pontuação do CNPJ e documento antes do nome na mesma linha.
- Atravessar logotipo/agência intercalados apenas com evidência próxima da conta de origem; manter a proteção contra CNPJ do emissor.
- Separar o cabeçalho do cliente da seção do fundo de investimento. Um fundo que seja o próprio titular continua permitido.
- Preservar o nome impresso, inclusive abreviado ou acompanhado de documento mascarado, sem completar dados a partir de solicitantes ou contrapartes.
- Reconhecer cabeçalhos mensais deslocados para depois dos movimentos na ordem textual.
- Recuperar corretamente cooperativa e conta quando os valores aparecem antes dos rótulos, evitando conflito artificial entre representações nativas.

### Ajustes após revisão da PR #595

Os dois apontamentos da revisão foram reproduzidos antes da correção, em PDF nativo e OCR:

- **Colunas e espaços internos:** os espaços duplos do nome não são mais tratados como novas células. A associação usa os limites impressos dos rótulos e valida o valor completo de cada campo. Valores excedentes, desalinhamento ou campos incompatíveis geram `ambiguous_header_columns`, com confiança ambígua e sem cadastro automático. A quantidade de partes separadas por espaços não é usada como alternativa para adivinhar colunas. Os testes também conferem o cliente e a conta efetivamente persistidos por `ReceivedStatements.receive`.
- **Fundo titular versus produto:** um título de investimentos e a palavra “FUNDO” não encerram o cabeçalho do titular. A delimitação do produto exige um titular independente já identificado, a referência mensal e a forma de apresentação do produto. O fundo titular é preservado antes ou depois da referência mensal; dois pares distintos de titular/documento continuam ambíguos.

As regressões adicionais cobrem números dentro do nome, coluna vazia, conta excedente, tipos incompatíveis, tabelas no corpo e titular que investe em outro fundo. Não houve mudança de versão publicada: a PR permanece na versão 7, ainda sem implantação.

A repetição dos 298 PDFs após esses ajustes preservou os nomes, documentos, bancos/agências/contas, cooperativas e níveis de confiança verificados na etapa anterior, inclusive os 34 exemplos manuais. Os novos casos da revisão são sintéticos e exercitam situações que não estavam representadas nessa coleção.

CPF/CNPJ ausente ou mascarado não é inventado. Documento malformado continua sujeito a revisão. Conflitos reais entre titulares continuam ambíguos. A extração de movimentos, valores, datas e conciliação não é alterada.

## Amostra e limites

Foram usados 298 PDFs únicos de uma coleção privada de recuperação/revisão: 257 nativos e 41 com OCR previamente capturado, cobrindo 52 perfis de leitura. A seleção manual contém 34 exemplos intencionais, dos quais 12 tiveram a primeira página também conferida visualmente.

A seleção não é aleatória e não estima a taxa de acerto geral em produção. O ensaio executa o reconhecedor de identidade com as representações salvas e leituras nativas alternativas, sem nova conversão completa, OCR remoto ou consulta cadastral. Não audita a associação histórica efetiva aos clientes no banco de produção.

Na conferência inicial, 10 documentos tinham nome e CNPJ corretos, 10 tinham resultado compatível com a informação disponível, 13 tinham lacunas/candidatos incorretos e um tinha ambiguidade a investigar. Essa última foi reproduzida como inversão de cooperativa e conta no texto.

Documentos, nomes, números fiscais, contas e relatórios detalhados permanecem no armazenamento local privado, fora do repositório. Os testes versionados usam nomes, documentos e contas sintéticos.

## Resultado da repetição local

| Campos preenchidos | Versão 6 | Versão 7 |
| --- | ---: | ---: |
| Nome e CPF/CNPJ | 105 | 119 |
| Somente nome | 91 | 111 |
| Somente documento | 3 | 0 |
| Nenhum dos dois | 99 | 68 |

Houve alteração em 43 dos 298 resultados. Os 105 pares de nome/documento previamente preenchidos foram preservados, assim como os 20 controles manuais. Os 13 exemplos com problemas melhoraram: seis recuperaram nome e CNPJ completos; os demais recuperaram nomes disponíveis ou excluíram candidatos incorretos. O caso adicional de conflito artificial entre cooperativa e conta foi resolvido.

Esses totais medem presença de campos, não acurácia geral. O exemplo com CNPJ impresso de 15 dígitos continua ambíguo e sem documento fiscal preenchido; seu nome preserva uma falha de grafia já presente no texto nativo extraído. Não foi acionado novo OCR nem corrigida a grafia por inferência. Nomes abreviados e máscaras continuam limitados ao conteúdo disponível.

## Compatibilidade com conversões existentes

Não há migração de banco nem mudança de contrato de API. O mecanismo existente de atualização de evidências usa a versão 7 para revisar uma vez PDFs ainda ativos e sem cliente associado. A atualização não usa candidatos do reconhecedor antigo como se fossem metadados independentes: isso perpetuaria o CNPJ do fundo e criaria conflitos falsos com nomes corrigidos.

Quando não há evidência nativa de identidade, o resultado e a confiança do OCR salvo são preservados. PDFs expirados não são lidos. Documentos já vinculados a clientes, inclusive confirmações manuais, não são reassociados. O payload financeiro e a expiração permanecem iguais.

## Validação reproduzível

```powershell
backend/venv/Scripts/python.exe -m pytest backend/tests/test_statement_identity_pr595_review.py backend/tests/test_statement_identity_audit_headers.py backend/tests/test_statement_identity_native_remaining.py backend/tests/test_statement_identity_native_headers.py backend/tests/test_statement_identity_native_continuations.py backend/tests/test_statement_holder_headers.py backend/tests/test_asaas_statement_identity.py -q
backend/venv/Scripts/python.exe -m ruff check backend
backend/venv/Scripts/python.exe scripts/test_backend.py
```

No ambiente Windows do Codex, os comandos pytest e o runner devem ser executados fora do sandbox, conforme `AGENTS.md`. O runner completo administra e remove seu próprio diretório temporário.

Os testes cobrem reconhecimento positivo, isolamento das contrapartes, CNPJ do emissor, solicitante, fundo investido versus fundo titular, máscara, documento malformado, conflitos entre titulares, atualização da versão anterior, idempotência, expiração e confirmações existentes.

A suíte direcionada passou com `153 passed`, incluindo 53 novos casos sintéticos na PR (22 deles adicionados após a revisão). O lint retornou `All checks passed!`.

A suíte completa após a revisão terminou com `5055 passed, 89 skipped, 2 xfailed, 2 warnings in 385.15s`, usando um processo selecionado automaticamente pela memória disponível. O diretório temporário do runner foi removido ao final. A execução inclui requisições HTTP reais de conversão, download de relatório e caminhos negativos; o endpoint atual é `/convert`, e `/analyze` permanece desativado com resposta 405.

## Implantação e reversão

Aplicar via PR e implantação normal, sem operação manual em dados de produção. Verificar após a implantação exemplos de cabeçalho em colunas e pendências ainda ativas; acompanhar avisos `pro_receipt_identity_recovery_failed` e o volume de revisões de identificação.

O risco principal é atribuição incorreta por novas formas de cabeçalho. A recuperação permanece limitada a rótulos, contexto da conta e evidência documental, com regressões negativas. Reverter o commit restaura o reconhecedor anterior sem alterar o esquema. Evidências e associações já persistidas não são desfeitas automaticamente por rollback; eventuais associações incorretas precisam de revisão explícita, sem remoção em massa.
