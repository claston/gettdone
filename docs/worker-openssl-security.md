# OpenSSL: avaliação temporária de CVE-2026-124709

## Incidente e alcance

Em 21/11/2026, a [release 44906846907](https://github.com/claston/gettdone/actions/runs/44906846907)
foi bloqueada pelo Trivy antes de assumir credenciais AWS ou publicar a imagem.
O achado é HIGH em `libssl3t64=3.5.7-1~deb13u3`. O build e o canário de
CSV/XLSX/OFX/PDF, PDFium e PostgreSQL passaram. Essa execução não atualizou produção.

O [aviso do OpenSSL](https://openssl-library.org/news/secadv/20261117.txt)
descreve retenção de memória em **servidores QUIC** quando um cliente abre e
reseta streams rejeitadas. O fornecedor classifica o problema como Low; o
relatório Trivy usa HIGH. Não alteramos a severidade nem o relatório.
O aviso agenda as releases corrigidas, incluindo 3.5.10, para 01/12/2026.
Em 21/11, o [Debian](https://security-tracker.debian.org/tracker/CVE-2026-124709)
ainda marca Trixie e Sid como vulneráveis, sem versão corrigida disponível.

## Avaliação e limites

O worker executa `awslambdaric` e `app.workers.conversion_lambda.lambda_handler`.
Consome a Runtime API da Lambda, AWS por boto3/botocore e PostgreSQL por psycopg.
A revisão de `backend/app/{workers,adapters,application}` não encontrou servidor
QUIC, socket de escuta ou chamada às APIs OpenSSL de servidor QUIC. O módulo
`ssl` do CPython 3.12 é usado para TLS sobre sockets de fluxo. A biblioteca
OpenSSL continua contendo código vulnerável; **não é uma correção binária**.

A exceção aceita apenas CVE-2026-124709, severidade HIGH, pacote e versão acima,
com runtime OpenSSL 3.5.7. Expira no início de **02/12/2026 UTC**. Ela não se
aplica à API, a outras imagens, versões, CVEs ou achados CRITICAL.

`scripts/check_worker_openssl.py` roda na imagem construída, sem escrita no
filesystem, antes dos scans de CI e deploy. Verifica:

- inventário Debian único, versão efetivamente carregada e caminhos de libssl/libcrypto;
- CPython 3.12 e as 23 versões de dependências do artefato revisado;
- fontes da aplicação sem referências a QUIC, sockets de servidor ou datagramas;
- consumidores nativos empacotados sem referências às APIs de servidor QUIC;
- certificados raiz e verificação TLS padrão disponíveis; rejeição de socket UDP por `ssl`.

Essas verificações detectam mudanças na arquitetura revisada; não são uma prova
formal de ausência de todo comportamento dinâmico. A busca em consumidores
nativos exclui as bibliotecas OpenSSL que definem as próprias APIs. Não declara
que QUIC foi removido ou desativado no OpenSSL. A revisão humana da arquitetura
continua necessária. Mudança de dependências exige nova avaliação, mesmo que
seja uma atualização transitiva sem alteração do manifesto.

O gate exige todos os campos de `openssl-runtime.json`; evidência ausente,
inválida ou reprovada bloqueia. Evidência de zlib não autoriza OpenSSL nem o
inverso. No ECR, também se exige que o digest escaneado seja o da imagem
construída e validada nessa execução. Relatórios originais, inventários,
canário e decisão com motivo/expiração ficam nos artefatos existentes.

## Validação e operação

Executar `scripts/test-backend.ps1`, Ruff e os checks de texto do projeto.
Os testes cobrem ausência/falha de evidência, expiração, versão/pacote/CVE/severidade
divergentes, digest ECR diferente e alterações em fontes/dependências nativas.
O workflow **CI | Worker Debian Image** constrói a imagem real, executa o
verificador, canário e Trivy. Este computador não tem Docker; a validação Linux
da imagem é feita nesse workflow. Merge e deploy de produção são etapas separadas.

Antes de 02/12, verificar a disponibilidade do pacote corrigido. Quando existir,
atualizar a imagem/pacote por versão verificável, validar runtime e scans e remover
a exceção, seu verificador e os argumentos correspondentes nos workflows. Não
renovar automaticamente o prazo se a distribuição atrasar a correção.

Não há alteração de dados, esquema, API, extração financeira ou bibliotecas
instaladas. Para rollback desta política, reverter o PR: a CVE volta a bloquear
novas releases. Isso não reverte uma imagem já implantada; usar o digest anterior
registrado pelo fluxo de deploy se um rollback de produção for necessário.
