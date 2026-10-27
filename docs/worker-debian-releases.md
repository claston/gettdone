# Releases padrão do worker Debian

`Dockerfile.lambda` passa a ser a definição de produção Debian distroless,
com as bases por digest e o cliente oficial `awslambdaric` já validados na
#488. `backend/requirements-worker.txt` é o manifesto usado pelo build e pelos
testes do worker. As dependências da API permanecem separadas.

Esta mudança inclui os arquivos de runtime, testes e compatibilidade da #488,
que já foi implantada por digest, mas ainda estava aberta no GitHub. Não inclui
as funcionalidades das PRs #489–#493. Os arquivos separados da candidata são
mantidos como referência histórica; as esteiras ativas usam o Dockerfile e o
manifesto canônicos de produção.

## Procedimento normal

1. Integrar esta padronização em `main` pelo fluxo de PR. Como ela inclui a
   base da #488, não é necessário integrar a candidata antiga separadamente.
2. Integrar as PRs das funcionalidades desejadas. A imagem será construída com
   o código desse commit de `main`, inclusive os arquivos novos do core/worker.
3. Aguardar o sucesso de **CI | Lint and Tests** e **CI | Worker Debian Image**
   no mesmo commit de `main`. O segundo workflow testa as dependências reais do
   worker, incluindo PyPDF 6, e constrói/testa/escaneia o Dockerfile de produção.
   As evidências do CI usam nome único por execução/tentativa. O arquivo completo
   da imagem é guardado somente em execução manual desse CI para uma eventual
   validação AWS isolada, evitando armazenar um tar a cada merge.
4. Executar **CD | Deploy Worker Image (ECR)** em `main`, com `deploy=true` e
   **`image_source=build-main`**, que permanece sendo o padrão:

   ```powershell
   gh workflow run deploy-worker-ecr.yml --ref main -f deploy=true -f image_source=build-main
   ```

5. Aprovar o ambiente `production-worker` no GitHub e acompanhar a conclusão.
   Merge não dispara deploy automaticamente. Não é necessário Docker local,
   acesso ao banco de PRD ou ao Render para essa operação.

Esse procedimento atualiza o worker. Funcionalidades que também mudem API,
frontend ou esquema de banco continuam seguindo os passos das respectivas PRs.

## Validação da imagem que será publicada

A esteira de release constrói a imagem Debian uma única vez localmente no
runner, sem publicar. Em seguida executa o cliente oficial da Lambda com dados
sintéticos CSV/XLSX/OFX/PDF, PDFium e uma transação SQL em PostgreSQL descartável
do próprio runner. O contêiner roda sem privilégios e com filesystem somente
leitura, escrevendo apenas em `/tmp`.

São gerados SBOM e relatório Trivy. HIGH ou CRITICAL, inclusive sem correção,
bloqueiam a release. Também é obrigatório reconhecer o inventário Debian e a
versão do PyPDF instalada, para impedir que um scan vazio pareça aprovado.
As evidências ficam no artefato `worker-debian-release-<run_id>-<attempt>`.

Somente depois desses passos a esteira assume a role AWS existente. Ela marca
e publica **a mesma imagem que foi testada**, sem um segundo build. Cada
tentativa recebe tag imutável `release-<sha12>-<run_id>-<attempt>`, evitando
reutilizar uma imagem Amazon Linux antiga ou sobrescrever uma tag anterior.
O gate ECR HIGH/CRITICAL continua sendo executado depois da publicação.

O canário de contêiner não substitui uma validação AWS isolada ao trocar a base
ou os componentes nativos do runtime. A base atual já passou pelo canário AWS
da #488 e por jobs reais em PRD. Alterações futuras nessa base exigem nova
avaliação; as funcionalidades de aplicação recebem os gates por commit acima.

## Atualização e rollback

O change set continua restrito a `ConversionWorker.Code.ImageUri`, sem
substituir a função nem mudar fila, outbox, IAM ou os demais parâmetros. O
deploy usa o URI por digest, não a tag.

Antes da troca, o artefato `worker-image-rollback-<run_id>-<attempt>` registra a
imagem que estava em produção. Para voltar, usar `previous_image_uri` em outro
change set, preservando template e demais parâmetros com `UsePreviousValue`.
O rollback da próxima release apontará para a imagem que estiver em PRD nesse
momento, não para um digest antigo fixado no código. Resultados já gravados não
são desfeitos e não há migração ou reprocessamento automático nesta mudança.

## Opção histórica da #488

`image_source=pr488-debian-canary` permanece disponível para selecionar somente
o digest aprovado `sha256:737cd4fb2e9cc40a92607358378ff408990bd67d4b00064735d0d5827213c11f`.
Ela não constrói uma imagem e não inclui PRs mescladas depois daquele build.
Não deve ser usada para publicar funcionalidades novas. Seu canário e seus
scans continuam vinculados à evidência original.
