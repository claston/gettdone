# Imagem de execução da API

O `Dockerfile` mantém Python 3.12/Debian slim e separa a instalação de
dependências da execução. Os estágios herdam a mesma base resolvida no build.
O estágio `dependencies` instala somente wheels em `/opt/venv`; o estágio
`runtime` recebe esse ambiente e os arquivos necessários à aplicação.
Compiladores, testes, corpus sintético, caches locais e ferramentas de lint
não entram na imagem. O contexto também usa uma lista explícita de inclusões.

`backend/requirements-runtime.txt` contém as dependências da API. O arquivo
`backend/requirements.txt` inclui essa lista e acrescenta pytest, xdist, Ruff
e pytesseract para desenvolvimento/CI, preservando o comando de instalação
existente. `httpx` continua em produção, pois o serviço de contato o utiliza.
Pillow passa a ser uma dependência explícita de API e worker; seu requisito
`>=8.0.0` mantém o intervalo anteriormente fornecido por pytesseract.

As imagens da API e do worker não incluem Tesseract nem pytesseract. Ambas
definem `PDF_OCR_ENABLED=false`. No deploy, manter esse valor e configurar
`TEXTRACT_ENABLED=true`, `AWS_REGION`, `TEXTRACT_TEMP_BUCKET` e as permissões
AWS existentes. Não definir `TEXTRACT_FORCE=true` apenas por causa desta
mudança: PDFs legíveis continuam utilizando a extração nativa.
O OCR local permanece disponível para desenvolvimento fora dessas imagens.

A API conserva SQLite, Alembic, `backend/alembic.ini`, as migrações e
`backend/scripts/run_alembic_migrations.py`. O worker conserva sua exclusão
de SQLite e seus controles de isolamento. Não há migração de banco ou
mudança de contratos HTTP, filas, armazenamento ou significado financeiro.
O frontend continua em `/app/frontend`, o código em `/app/backend/app`,
e a inicialização aceita `APP_RELEASE` e `PORT` como antes.

## Validação

`CI | API Runtime Image` constrói o Dockerfile real sem publicar a imagem.
Um script montado somente para o teste verifica inventário, ausência de
compiladores/OCR local, importação da API, SQLite, arquivos de migração,
CSV/XLSX/OFX/PDF, exportações e renderização PDFium/Pillow. Os cenários
Textract substituem somente a fronteira externa: preservam valores e páginas
originais e verificam que uma falha do provedor não chama OCR local.
Essa etapa não tem rede e não acessa AWS nem documentos de clientes.

Um segundo contêiner usa a inicialização padrão, `APP_ENV=production` e
`PORT=8080`, com configuração sintética. As requisições HTTP reais verificam
saúde, frontend, `POST /convert`, relatórios e um relatório inexistente.
`POST /analyze` já foi removido da aplicação; o teste mantém sua resposta 405.
Os relatórios JSON, logs e metadados/tamanho da imagem ficam nos artefatos da CI.
O workflow do worker executa o mesmo ensaio Textract em sua imagem final,
além dos controles existentes de PostgreSQL, SBOM e vulnerabilidades.

Antes da promoção, confirmar os dois workflows de imagem e a CI completa.

A integração da `main` no commit `106d82f` acrescentou Documento à branch.
O gate do push compara com `dd64a92`, o commit anterior à integração, enquanto
a PR compara com a `main`. As 3.304 diferenças PDF e 3.642 de revisão foram
conferidas integralmente e são as mesmas adições Documento já enumeradas.
Os vínculos foram atualizados para essa base e a árvore
`7a82a42455bed4a8ba859eeaaf00378344908b2b`; a evidência e os hashes estão em
[api-runtime-image-ci-revalidation.json](api-runtime-image-ci-revalidation.json).
Para reproduzir o push, executar ambos os gates com `--base-ref dd64a92`
e `--approvals docs/transaction-document-approvals.json`. Comparadores,
baselines e oráculos financeiros permanecem intactos.

Esta alteração não faz deploy. O rollback consiste em restaurar o digest
anterior da imagem, preservando variáveis, volumes e configurações AWS.
Não há rollback de dados. A imagem anterior da API volta a incluir Tesseract;
manter `PDF_OCR_ENABLED=false` também durante esse rollback.
