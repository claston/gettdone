# Histórico da candidata Debian e integração no runtime padrão

A candidata usa Python 3.12 em Debian Trixie distroless e o Runtime Interface
Client oficial `awslambdaric`. As bases e o cliente estão fixados por digest/versão.
Na #488, a imagem foi validada separadamente e depois promovida por digest.
A padronização incorpora essa base em `Dockerfile.lambda` e usa
`backend/requirements-worker.txt` para as próximas releases. Os arquivos
separados da candidata ficam como referência histórica. O procedimento atual
está em [Releases padrão do worker Debian](worker-debian-releases.md).

O build parte do Python oficial slim; o runtime usa distroless `cc-debian13`
sem shell, gerenciador de pacotes ou ferramentas de administração. São copiados
o interpretador, a biblioteca padrão e as bibliotecas nativas necessárias.
Os pacotes nativos adicionais preservam seu status original do dpkg e as
licenças, permitindo inventário e scan reais. Nenhum banco de pacotes é apagado.
Os módulos opcionais de terminal (`curses`, `readline`), GUI (`tkinter`) e a extensão `_uuid`
ficam fora; `uuid` mantém o fallback Python e a geração por `os.urandom`.
Todas as extensões nativas restantes são carregadas durante o build para
detectar dependências ausentes. O worker não oferece terminal interativo.

O PyPDF da candidata é 6.21.2, corrigindo os achados detectados no 5.4.0.
Na validação inicial, essa mudança ficou restrita à candidata. No padrão atual,
o worker usa essa versão e roda a suíte completa com suas dependências reais;
as dependências do servidor API continuam separadas.
O código de extração e preflight preserva explicitamente a falha por fonte
nula, pois o PyPDF novo pode retornar texto incompleto nesse caso. O sinal
de falha e o motivo da tentativa de OCR continuam compatíveis com o PyPDF atual.

## Validação na esteira

O workflow `CI | Worker Debian Image` constrói `Dockerfile.lambda`
para `linux/amd64`, sem credenciais AWS. O contexto de build segue a mesma
lista restrita do worker atual, excluindo a API, administração e segredos.

O cliente oficial recebe uma invocação por uma implementação local da Runtime
API. O contêiner roda como usuário sem privilégios, com sistema de arquivos
somente leitura e escrita apenas em `/tmp`. A invocação verifica:

- O contrato de isolamento do worker e a importação do handler de produção.
- Conversões sintéticas CSV, XLSX, OFX e PDF pelo mesmo núcleo da aplicação.
- Quantidade e valores dos lançamentos, exportação OFX e leitura do XLSX.
- Renderização nativa pelo PDFium e carregamento do cliente binário PostgreSQL.
- Uma transação SQL em tabela temporária no PostgreSQL descartável da esteira.

O handler de canário aceita somente `{"contract":"worker-image-v1"}` e exige
`WORKER_IMAGE_CANARY=1`. Não recebe documentos do usuário nem consome filas.
A conexão opcional de teste permite somente localhost, usuário e banco
`worker_canary`; não utiliza `DATABASE_URL` da aplicação.

O Trivy examina pacotes do sistema e bibliotecas Python. Qualquer achado HIGH
ou CRITICAL, inclusive sem correção, bloqueia o artefato de imagem aprovado.
Não há exceção por CVE nem `ignore-unfixed`. O relatório JSON e o SBOM são
preservados mesmo quando o scan bloqueia a candidata. O arquivo Docker é
gerado somente depois do sucesso dos testes e do scan, incluindo a confirmação
de que o scanner reconheceu o inventário Debian e a versão candidata do PyPDF.

## Promoção e rollback

O teste da Runtime API na esteira não substitui uma invocação na AWS Lambda.
Antes da promoção, publicar o artefato aprovado com uma tag exclusiva de teste no ECR,
aguardar também o scan do ECR e invocar uma função temporária isolada:

1. Usar exatamente o digest aprovado, arquitetura x86_64 e uma role mínima.
2. Não conectar filas, banco de produção ou credenciais de clientes.
3. Sobrescrever `ImageConfig.Command` com
   `app.workers.worker_image_canary.lambda_handler` e habilitar o canário.
4. Invocar com o evento fixo, confirmar resposta `status=passed`, os seis
   checks e ausência de erro de inicialização. O PostgreSQL remoto fica
   `not_configured`; a transação já foi verificada na esteira.
5. Remover a função, logs e recursos temporários após registrar a evidência.

Somente após essa evidência, alterar a imagem de produção em uma promoção
revisável. Preservar o gate HIGH/CRITICAL do ECR, os parâmetros atuais da stack
e a configuração da fila. A candidata mantém o handler e as variáveis do
worker; não altera banco nem mensagens SQS. O rollback restaura o digest
anterior pela stack CloudFormation, sem migração de dados. Não apagar a imagem
anterior antes de concluir a observação da nova versão.

Docker local não é necessário para esse fluxo; o build ocorre no GitHub Actions.

## Canário AWS aprovado em 26/10/2026

A invocação real foi executada às 07:29 (America/Sao_Paulo), em `us-east-1`,
na função temporária `gettdone-worker-image-canary`, arquitetura `x86_64`,
com o digest aprovado:

`sha256:737cd4fb2e9cc40a92607358378ff408990bd67d4b00064735d0d5827213c11f`

O `ResolvedImageUri` da Lambda confirmou esse digest. A invocação retornou
HTTP **200**, sem `FunctionError`, e a resposta:

```json
{"status":"passed","checks":["csv","xlsx","ofx","pdf","pdfium","psycopg"],"postgres":"not_configured"}
```

O PostgreSQL remoto não foi configurado; a transação SQL já passou no banco
descartável da esteira. O request ID foi
`fe627a89-d313-4736-a5b3-2c96a1f3dc6d`: duração de 7.845,81 ms,
inicialização de 1.797,05 ms e memória máxima usada de 114 MB (1.024 MB alocados).

A função usou somente a role exclusiva
`/gettdone/GettDoneWorkerImageCanaryRole`, com `AWSLambdaBasicExecutionRole`,
e as variáveis `WORKER_IMAGE_CANARY=1`, `TEXTRACT_ENABLED=false`,
`PDF_OCR_ENABLED=false` e `AWS_EC2_METADATA_DISABLED=true`.
Não recebeu banco, bucket ou credenciais de produção, nem filas ou tráfego real.
A policy do repositório ECR já permitia a leitura pela Lambda e não foi alterada.

A função, a role e o grupo de logs criados para o teste foram excluídos com
sucesso; uma consulta posterior confirmou `ResourceNotFoundException` para
a função. A imagem no ECR foi preservada. As evidências locais estão em
`tmp/worker-lambda-canary-5d7b/` (configuração, resposta e log da invocação).
O usuário deve remover a policy temporária `GettDoneWorkerImageCanaryTemporary`
do bootstrap; essa credencial não tem permissão para remover a própria policy.

Esse canário fundamentou a promoção por digest da #488, concluída pela execução
40411630896 em 26/10/2026. A padronização mantém aprovação e rollback por digest
e acrescenta o build Debian do commit de `main` para funcionalidades novas.
