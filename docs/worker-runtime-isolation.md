# Isolamento e publicação manual do worker

## Fronteira do runtime

A imagem Lambda compartilha o core de conversão com a aplicação web, mas não
contém nem importa as superfícies HTTP e administrativas. O contrato é validado
durante o build por `app.workers.worker_image_contract`.

Devem permanecer fora da imagem:

- frontend e rotas FastAPI;
- administração, autenticação e sessões;
- checkout, contato e Google OAuth;
- ferramentas de desenvolvimento e migração;
- dependências exclusivas do servidor web.

O worker usa `PostgresConversionAccessService`, que expõe somente operações de
quota e histórico necessárias ao processamento. Ele não autentica usuários e
não precisa de `ACCESS_CONTROL_TOKEN_SECRET`.

O template de infraestrutura também deve remover essa variável do ambiente da
Lambda. Publicar somente a nova imagem não apaga variáveis já configuradas na
função.

O runtime padrão usa Debian distroless com bases fixadas por digest e o cliente
oficial da Lambda. O inventário Debian e as licenças das bibliotecas nativas
são preservados. Os testes do runtime, SBOM e gate Trivy antecedem a publicação;
o gate ECR também bloqueia findings `CRITICAL` ou `HIGH` antes da atualização.
Esses bloqueios não devem ser ignorados.

O fluxo recomendado roda inteiramente no GitHub Actions e está descrito em
[Releases padrão do worker Debian](worker-debian-releases.md). Os exemplos
locais abaixo são opcionais e não exigem instalar Docker para o fluxo normal.

## Build e validação local

Execute na raiz do repositório:

```powershell
$CommitSha = git rev-parse HEAD
docker build --platform linux/amd64 -f Dockerfile.lambda `
  --build-arg APP_RELEASE=$CommitSha `
  -t gettdone-worker:manual-$($CommitSha.Substring(0, 12)) .

docker run --rm --entrypoint /usr/local/bin/python3.12 `
  gettdone-worker:manual-$($CommitSha.Substring(0, 12)) `
  -m app.workers.worker_image_contract
```

O build deve falhar se encontrar código administrativo, módulos web ou
distribuições proibidas.

## Publicação manual no ECR

Exemplo em PowerShell, ajustando apenas o perfil caso necessário:

```powershell
$AwsProfileName = "gettdone-iac"
$AwsRegionName = "us-east-1"
$FoundationStackName = "gettdone-production-foundation"
$CommitSha = (git rev-parse HEAD).Trim()
$ImageTag = "release-$($CommitSha.Substring(0, 12))"

$RepositoryUri = (aws cloudformation describe-stacks `
  --profile $AwsProfileName `
  --region $AwsRegionName `
  --stack-name $FoundationStackName `
  --query "Stacks[0].Outputs[?OutputKey=='ConversionImageRepositoryUri'].OutputValue | [0]" `
  --output text).Trim()
$RegistryHost = $RepositoryUri.Split('/')[0]
$RepositoryName = $RepositoryUri.Substring($RepositoryUri.IndexOf('/') + 1)

aws ecr get-login-password --profile $AwsProfileName --region $AwsRegionName |
  docker login --username AWS --password-stdin $RegistryHost

docker tag "gettdone-worker:manual-$($CommitSha.Substring(0, 12))" "$RepositoryUri`:$ImageTag"
docker push "$RepositoryUri`:$ImageTag"

$ImageDigest = (aws ecr describe-images `
  --profile $AwsProfileName `
  --region $AwsRegionName `
  --repository-name $RepositoryName `
  --image-ids "imageTag=$ImageTag" `
  --query "imageDetails[0].imageDigest" `
  --output text).Trim()
$ImmutableImageUri = "$RepositoryUri@$ImageDigest"
Write-Output $ImmutableImageUri
```

1. Autentique o perfil AWS utilizado para a infraestrutura.
2. Consulte o URI do repositório no output `ConversionImageRepositoryUri` da
   stack de foundation.
3. Faça login no registry com `aws ecr get-login-password` e `docker login`.
4. Marque a imagem com uma tag imutável baseada no commit, por exemplo
   `release-<12 caracteres do commit>`.
5. Envie a imagem ao ECR.
6. Aguarde o scan-on-push e confira manualmente os findings antes do deploy.
7. Consulte o digest publicado e forme o URI como
   `<repositorio>@sha256:<digest>`. Não faça deploy usando apenas a tag.

## Atualização manual da Lambda

A função é gerenciada por CloudFormation. Para evitar drift, não use
`aws lambda update-function-code` diretamente.

1. Crie uma atualização da stack do worker usando o template atual.
2. Preserve todos os parâmetros existentes.
3. Altere somente `ConversionImageUri` para o URI por digest.
4. Revise o change set e confirme que apenas `ConversionWorker.Code.ImageUri`
   será modificado, sem substituição da função.
5. Mantenha o gatilho da fila no estado atual durante a troca da imagem.
6. Execute o change set e aguarde `UPDATE_COMPLETE`.
7. Faça um canário com arquivo sintético e confirme job, artefato no S3 e logs
   estruturados no CloudWatch antes de ampliar o consumo da fila.

## Rollback

Registre o digest anterior antes da atualização. Em caso de falha, crie outro
change set alterando somente `ConversionImageUri` para o digest anterior e
confirme novamente que não há mudanças adicionais.
