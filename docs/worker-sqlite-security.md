# Remoção de SQLite não utilizado da imagem do worker

## Problema e escopo autorizado

O scan do worker do PR #533 bloqueou `CVE-2026-119373` (HIGH) no pacote
`libsqlite3-0 3.46.1-7+deb13u2`, copiado da imagem Python Debian para o runtime
distroless. O usuário autorizou corrigir a imagem de produção, preservando os
testes e o scanner bloqueante. Esta mudança prepara e valida a imagem; não
executa merge, deploy ou mudanças de banco.

O [Debian Security Tracker](https://security-tracker.debian.org/tracker/CVE-2026-119373)
lista o pacote como vulnerável e sem correção Debian disponível na investigação.
O upstream identifica a correção em SQLite 3.54.0. Não é necessário introduzir
uma compilação própria ou trocar a distribuição: o worker não utiliza SQLite.

## Evidência de uso e compatibilidade

Uma busca por `sqlite3`, `aiosqlite`, `pysqlite` e `FTS5` em `backend/app` encontrou
uso apenas em `application/access_control/access_control_db.py`. Esse diretório
da API já é excluído dos Dockerfiles/contextos e proibido pelo contrato do worker.
Os workers utilizam PostgreSQL, S3 e armazenamento de análises em arquivos; os
caminhos de conversão e diagnóstico não importam SQLite.

Um teste em processo novo bloqueia qualquer importação de `sqlite3`, `_sqlite3`
e submódulos, importa os dois handlers de produção e executa o canário sintético
completo: CSV, XLSX, OFX, PDF, renderização PDFium e cliente PostgreSQL binário.
Isso verifica que a dependência pode ser removida sem mudar os fluxos suportados.
O canário real em contêiner também executa uma transação no PostgreSQL descartável
da CI e confere valores e exportações.

| Componente | Resultado |
| --- | --- |
| API e suas bases SQLite | Sem alteração; `Dockerfile` da API, controle de acesso e dados existentes preservados |
| Núcleo de conversão, parsers/YAML/OCR/exportação | Sem alteração |
| SQLite no runtime do worker | Biblioteca nativa, pacote Python e extensão `_sqlite3` ausentes |
| Bases Debian/Python, cliente Lambda e demais dependências | Mesmos digests e versões |
| PostgreSQL, S3, filas, identidade e TTL | Sem mudança de contrato ou configuração |

## Construção e inventário verdadeiros

`Dockerfile.lambda` e sua referência `Dockerfile.lambda.debian` deixam de copiar
`libsqlite3-0` para o runtime final. O pacote `sqlite3` da biblioteca padrão e a
extensão `_sqlite3*.so` também são removidos da árvore Python copiada. A imagem
de build continua separada; seus pacotes não são artefatos de execução.

O contrato de imagem falha se qualquer módulo SQLite ainda puder ser localizado
ou se `ctypes.CDLL("libsqlite3.so.0")` conseguir carregar a biblioteca. Essa
verificação roda no build e na invocação do canário. Uma inclusão futura de
SQLite exige revisar explicitamente a arquitetura/dependência e sua segurança.

Os arquivos de status e licenças dos pacotes que realmente permanecem no runtime
continuam copiados sem alteração. Não há edição de versões, exclusão de registros
de bibliotecas ainda instaladas, `.trivyignore`, VEX, `ignore-unfixed` ou supressão
de CVE. HIGH/CRITICAL continuam bloqueando CI e release. A ausência do componente
no inventário deve corresponder à ausência real da biblioteca e dos bindings.

## Validação

Na raiz, sempre pelo venv e fora do sandbox Windows:

```powershell
backend\venv\Scripts\python.exe -m pytest backend/tests/test_worker_sqlite_boundary.py backend/tests/test_worker_runtime_boundary.py backend/tests/test_worker_image_canary.py backend/tests/test_worker_candidate_workflow.py -q --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-worker-sqlite-unit
# 22 passed in 3.77s.

backend\venv\Scripts\python.exe -m pytest -q -n 4 --dist=worksteal --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-worker-sqlite-full --junitxml=tmp/worker-sqlite-full.xml
# 3959 passed, 7 skipped, 2 xfailed, 8 warnings in 235.41s.

backend\venv\Scripts\python.exe -m ruff check backend
backend\venv\Scripts\python.exe scripts/lint_frontend_text.py
backend\venv\Scripts\python.exe scripts/lint_frontend_navigation.py
```

Os testes foram escritos primeiro: seis falhas esperadas comprovaram que os
Dockerfiles ainda incluíam SQLite e que o novo contrato ainda não existia. O
teste de conversões com imports bloqueados já passou antes da implementação,
confirmando o diagnóstico de dependência não utilizada.

Os sete skips são seis ensaios PostgreSQL sem DSN descartável e um OCR real sem
Tesseract local. Os dois xfails são gaps conhecidos do corpus sintético. Os
diretórios temporários específicos são removidos após a validação. A CI executa
o PostgreSQL descartável do canário independentemente desses skips locais.

O workflow `CI | Worker Debian Image` é a evidência de imagem executável:
dependências reais do worker, build Linux/amd64, carga das extensões nativas,
Runtime Interface Client oficial, canário sem privilégios e filesystem somente
leitura, transação PostgreSQL, SBOM e Trivy com inventário reconhecido. Docker
local não está disponível neste ambiente Windows; não se substitui esse ensaio
por um teste estático de Dockerfile. Os resultados e links finais ficam no PR.

## Publicação e rollback

Após revisão/merge, seguir `docs/worker-debian-releases.md` para construir e
validar a imagem do commit de main, promover por digest e preservar o digest
anterior. Esta entrega não publica no ECR nem troca o worker de produção.
Não há migration, backfill ou mudança de arquivos de análise.

Rollback operacional usa o digest anterior conforme o runbook; ele pode restaurar
a dependência vulnerável conhecida e deve ser tratado como contingência. A
preferência é corrigir qualquer incompatibilidade sem reintroduzir SQLite no
worker. A API continua usando seu SQLite independentemente deste rollback.
