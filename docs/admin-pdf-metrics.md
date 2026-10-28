# PDFs únicos e período documental no Admin

O Admin separa tentativas de conversão e documentos PDF únicos. Dois envios com
o mesmo SHA-256 contam como duas tentativas e um documento. A deduplicação
considera os filtros de data e identidade selecionados, inclusive quando o
mesmo conteúdo foi enviado por uma conta e por um visitante anônimo.

## Leitura dos quadros

- **PDFs únicos com falha:** hashes distintos que tiveram pelo menos uma falha
  ou recusa no recorte. Bloqueios de quota/tamanho ficam fora, como no painel
  existente. Tentativas em processamento não entram nos novos quadros.
- **PDFs únicos em revisão:** hashes distintos de conversões concluídas cuja
  avaliação de qualidade registrada pede atenção. A indicação de captura no
  bucket de recuperação não substitui essa avaliação.
- Os grupos podem se sobrepor. Um PDF pode falhar, ser reenviado e ir para
  revisão. Um sucesso posterior não apaga as ocorrências anteriores.
- A tabela por dia usa `America/Sao_Paulo`, independentemente da pasta UTC do S3.
- A tabela por documento mostra referência protegida, primeiro/último horário
  de falha e revisão, contagens de tentativas e período identificado. Exibe até
  200 documentos, com total e limite explícitos; os contadores abrangem todo o
  recorte. O CSV de atenção mantém todas as tentativas dos últimos sete dias.
- Sem hash válido, a tentativa permanece nas contagens de tentativas e em
  `Sem hash`, mas não é apresentada como documento único comprovado. Nomes de
  arquivo, identificadores de usuários anônimos e caminhos S3 não são expostos.
- PDFs visualmente iguais com bytes diferentes têm hashes diferentes. O número
  mede arquivos idênticos, não equivalência visual ou contábil.

## Mês e período do documento

Os produtores persistem apenas três campos opcionais no histórico de contas e
visitantes: `statement_period_start`, `statement_period_end` e
`statement_period_basis`. Não são armazenados novos PDFs ou lançamentos.

`document_metadata` identifica um intervalo explícito do documento. Nos sucessos,
há preferência por um cabeçalho rotulado e inequívoco, seguido dos metadados já
extraídos. Na ausência deles, `movement_dates` usa as datas do livro completo,
com indicação de estimativa. Não usa o mês do upload nem vínculos editáveis do PRO.

Nas falhas, um intervalo rotulado `Período` nas duas primeiras páginas pode ser
preservado a partir das evidências de extração ou do texto nativo. Não há nova
chamada a OCR/IA nem tentativa de concluir a conversão. Datas de emissão ou
movimentos isolados não viram cobertura documental. Campos ilegíveis, inválidos,
conflitantes ou ausentes ficam como `Não identificado`.

Intervalos com vários meses ocupam uma linha própria na tabela mensal. Não se
multiplicam as conversões pelos meses abrangidos. Contagens únicas por dia ou
intervalo podem se sobrepor e não devem ser somadas como um total global.

O histórico novo preserva esses campos após o resultado temporário expirar.
Para registros anteriores sem período, o Admin consulta somente o pequeno
objeto `organization_source.period` de resultados ainda válidos, correspondendo
identidade e tentativa. A leitura não faz backfill nem recupera PDFs do S3.
Resultados antigos já expirados continuam com período desconhecido.

O período recuperado de resultados antigos é normalizado antes da resposta:
somente início, fim e origem são expostos. Campos auxiliares, como o booleano
`document_coverage` e o texto `evidence`, continuam no resultado original.
Isso mantém a resposta compatível com os campos de texto esperados pelo Admin.

## Implantação e compatibilidade

1. Executar a migração `20261028_01` antes de publicar os novos produtores/API:

   ```powershell
   Set-Location backend
   .\venv\Scripts\python.exe -m alembic upgrade head
   ```

   Usar o `DATABASE_URL` e `DATABASE_SCHEMA` do ambiente de destino. Em ambientes
   gerenciados, executar o comando no virtualenv/runtime de implantação.

2. A migração adiciona seis colunas TEXT anuláveis, três em cada tabela
   (`user_conversions` e `anonymous_conversion_events`), sem default, backfill,
   exclusão ou alteração de campos existentes. Não há criação de índice ou
   atualização de toda a tabela. O `ALTER TABLE` requer um bloqueio breve;
   aplicar fora de transações longas e observar bloqueios na implantação.
3. Publicar API e worker da mesma versão. O worker antigo continua gravando nas
   tabelas expandidas, mas não preenche o período. A nova API/worker exige a
   migração aplicada. Publicar também o frontend com a nova versão de `admin.js`.
4. Conferir `/admin/dashboard` autenticado e o CSV: um mesmo PDF enviado duas
   vezes deve incrementar tentativas duas vezes e documentos únicos uma vez;
   verificar o mês documental e a origem das datas. Não usar documentos de
   clientes na validação automatizada.
5. Acompanhar erros de persistência e respostas 5xx do Admin, proporção de
   tentativas sem hash e de períodos não identificados. Desconhecidos históricos
   não significam, isoladamente, falha na implantação.

## Rollback

Reimplantar API, worker e frontend anteriores, mantendo as colunas novas. O
downgrade Alembic desta revisão não remove dados ou colunas: permite a convivência
de versões e a reaplicação idempotente da migração. Não remover as colunas com
produtores novos em execução. Não há backfill ou reprocessamento remoto a desfazer.

## Validação reproduzível

Usar o virtualenv do projeto para Ruff, pytest e os scripts de lint. No Windows,
pytest precisa rodar fora do sandbox conforme `AGENTS.md`, com `--basetemp`
específico e limpeza ao final. `scripts/smoke_admin_pdf_metrics.py` usa apenas
dados fictícios e intercepta todas as requisições do navegador; salva capturas
em `tmp/admin-pdf-metrics/`.

Os testes PostgreSQL em `test_statement_period_migration.py` exigem
`ADMIN_METRICS_TEST_DATABASE_URL` apontando para uma base local descartável.
Validam upgrade, escrita antiga, rollback sem perda, reaplicação e leitura real
do histórico pelo Admin após a cadeia completa de migrações.
