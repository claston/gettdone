# Imagem candidata do worker: Debian distroless

A candidata usa Python 3.12 em Debian Trixie distroless e o Runtime Interface
Client oficial `awslambdaric`. As bases e o cliente estão fixados por digest/versão.
`Dockerfile.lambda` e o workflow de deploy continuam usando a imagem atual;
esta alteração não publica nem atualiza a função de produção.

O build parte do Python oficial slim; o runtime usa distroless `cc-debian13`
sem shell, gerenciador de pacotes ou ferramentas de administração. São copiados
o interpretador, a biblioteca padrão e as bibliotecas nativas necessárias.
Os pacotes nativos adicionais preservam seu status original do dpkg e as
licenças, permitindo inventário e scan reais. Nenhum banco de pacotes é apagado.
Os módulos opcionais de terminal (`curses`, `readline`) e a extensão `_uuid`
ficam fora; `uuid` mantém o fallback Python e a geração por `os.urandom`.
Todas as extensões nativas restantes são carregadas durante o build para
detectar dependências ausentes. O worker não oferece terminal interativo.

O PyPDF da candidata é 6.21.2, corrigindo os achados detectados no 5.4.0.
Essa mudança fica restrita à candidata e roda a suíte completa separadamente
na esteira; os arquivos de dependências de produção não mudam nesta PR.

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
