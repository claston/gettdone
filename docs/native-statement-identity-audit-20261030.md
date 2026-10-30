# Identificação de titulares em PDFs nativos — 30/10/2026

A identificação de cliente, CPF/CNPJ e conta podia perder campos legíveis ou
interpretar rótulos como nomes. A correção amplia a política compartilhada de
cabeçalhos e mantém identificações conflitantes pendentes de revisão.

## Amostra e método

- Listagem de 304 PDFs originais disponíveis; seleção dos 100 objetos PDF mais
  recentes em `conversion/jobs/`, sem usar os PDFs de resultados como novos uploads.
- A amostra contém 89 conteúdos distintos por SHA-256. As contagens por objeto
  incluem reenvios; não são uma estimativa estatística de todos os clientes.
- Download somente de S3, sem escrita remota, acesso ao banco ou reprocessamento
  em produção. Documentos, chaves S3, texto e identidades reais permanecem em
  `local/native-identity-audit-20261030/`, ignorado pelo Git.
- Extração local de texto nativo; nenhuma chamada a OCR, Textract ou outros
  serviços de reconhecimento. O executor privado desativa OCR e bloqueia a
  criação de clientes AWS diferentes de S3 e os pontos de entrada de OCR.
- PDFs com página sem texto suficiente são excluídos integralmente desta fase.
  Isso evita apresentar leitura parcial de um PDF misto como auditoria completa.
- Comparação do reconhecedor anterior e do corrigido sobre os mesmos arquivos;
  conferência dos cabeçalhos e comparação dos dados financeiros produzidos.

## Resultados

Dos 100 objetos, 66 converteram com texto nativo (64 conteúdos distintos), 15
foram excluídos por página sem texto suficiente, 18 tiveram erro de conversão
nativa e 1 estava protegido por senha. Os erros de conversão preexistentes não
foram tratados como identificação bem-sucedida nem corrigidos nesta entrega.

Nos mesmos 66 objetos convertidos:

| Campo preenchido | Antes | Depois |
| --- | ---: | ---: |
| Nome do titular | 9 | 54 |
| CPF/CNPJ do titular | 0 | 29 |
| Número da conta | 42 | 63 |
| Agência | 36 | 48 |

Cooperativa continua separada da agência. Os 9 nomes anteriores incluíam um
falso positivo de rótulo vazio, eliminado pela correção. As contagens representam
extração de campos, sem afirmar que todas as contas foram vinculadas no banco.

A conferência encontrou nome legível em 58 documentos, CPF/CNPJ em 30 e conta
em 63. Quatro nomes em cabeçalho postal sem rótulo inequívoco de titular e um
CPF/CNPJ em colunas deslocadas continuam sem reconhecimento. Outros documentos
não fornecem todos os campos no texto nativo disponível. Não há reconhecimento
universal nem preenchimento de informação ausente por suposição.

A comparação dos 66 resultados financeiros não encontrou diferenças em
transações, datas, valores, sinais, saldos, metadados bancários da análise e
referências de origem. Para a última conferência de identificação, os metadados
dessas conversões locais foram reutilizados; somente a identificação foi
reexecutada com a versão final das regras. Nenhum resultado foi gravado em
produção. O executor registrou zero chamadas a OCR ou Textract.

## Comportamento corrigido

1. Datas e horas de emissão deixam de interromper prematuramente o cabeçalho.
2. Rótulos vazios não consomem outro rótulo como nome de titular.
3. Campos sem dois-pontos, rótulos pontilhados, cooperativa abreviada e valores
   na linha seguinte passam pela mesma política de extração.
4. Nome e CPF/CNPJ podem ser associados quando a relação documental com a conta
   de origem é explícita; dados de banco emissor e contrapartes são excluídos.
5. Campos concatenados, tabelas agência/conta, conta composta por agência/produto/
   número e pontuação nativa conhecida preservam zeros e dígitos verificadores.
6. Identificações incompletas podem usar uma segunda leitura de texto e de
   coordenadas nativas. A leitura não chama OCR e não altera o parser financeiro.
   Referências dessa recuperação registram `text_mode: plain` ou `layout`.
7. Títulos, meses, paginação, logotipos com letras espaçadas e expressões como
   “Minha conta” não viram nomes de cliente. Representações conflitantes exigem
   revisão; metadados sem evidência não apagam um identificador impresso.
8. Rodapés de atendimento e copyright não fornecem o nome ou CPF/CNPJ do titular.
   O CNPJ do emissor não substitui o CNPJ do cliente em um cabeçalho de conta.

## Compatibilidade e armazenamento

`organization_source` mantém seu contrato aditivo e passa à versão 3. O mesmo
reconhecimento nativo é usado na nova conversão e na recuperação dos documentos
antigos ainda disponíveis e sem cliente confirmado. Vínculos manuais, resultados
financeiros, cotas e expiração permanecem protegidos pelas regras existentes.

Não há migração de schema, alteração de retenção ou novos serviços externos. A
segunda leitura pode aumentar o uso local de CPU em PDFs com identificação
incompleta. O rollback de código interrompe o uso da nova política; vínculos já
gravados precisam de revisão contextual, pois não são desfeitos automaticamente.

## Reprodução sem dados de clientes

Os testes em `backend/tests/test_statement_identity_native_headers.py` usam
somente nomes e identificadores sintéticos. Há casos de cabeçalhos deslocados
em um PDF nativo real gerado pelo teste, conflitos, falsos titulares, origem de
conta, separação de cooperativa/agência e proveniência.

Executar no venv, dentro de `backend`:

```powershell
venv/Scripts/python.exe -m pytest tests/test_statement_identity_native_headers.py tests/test_statement_holder_headers.py tests/test_received_statements.py tests/test_received_statements_http.py tests/test_pdf_statement_identity.py tests/test_pdf_statement_identity_http_real_api.py tests/test_conversion_job_executor.py -q
venv/Scripts/python.exe -m ruff check .
```

No ambiente Windows do Codex, executar pytest fora do sandbox com `--basetemp`
específico e remover o diretório exato após os testes, conforme `AGENTS.md`.
Os testes HTTP exercitam criação de lote, worker, reconhecimento persistido,
recebimentos, `POST /convert`, relatório disponível e relatório inexistente.
`POST /analyze` retorna 405 porque a rota está desativada na aplicação atual.

Validação da entrega: suíte completa com **3.640 passed, 6 skipped, 2 xfailed**;
grupo focado com **102 passed**; Ruff com **All checks passed!**; lint de texto
frontend com **Frontend text lint passed.** O `--basetemp` da suíte completa
ficou em `C:/Users/erica/AppData/Local/Temp/gettdone-pytest-native-identity-delivery`
e foi removido após a validação.
