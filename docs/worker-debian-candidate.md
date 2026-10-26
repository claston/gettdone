# Imagem candidata do worker: Debian slim

A candidata usa Python 3.12 em Debian Trixie slim e o Runtime Interface Client
oficial `awslambdaric`. A base e o cliente estão fixados por digest/versão.
`Dockerfile.lambda` e o workflow de deploy continuam usando a imagem atual;
esta alteração não publica nem atualiza a função de produção.

## Validação na esteira

O workflow `CI | Worker Debian Candidate` constrói `Dockerfile.lambda.debian`
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
gerado somente depois do sucesso dos testes e do scan.

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
