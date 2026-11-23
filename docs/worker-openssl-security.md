# OpenSSL: avaliações temporárias de aplicabilidade no worker

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

## Achados adicionais do ECR em 21/11/2026

A [execução 44970863088](https://github.com/claston/gettdone/actions/runs/44970863088)
publicou a imagem, mas parou antes de criar ou executar o change set de produção.
O ECR apontou sete HIGH adicionais no mesmo `libssl3t64=3.5.7-1~deb13u3`.
A exceção anterior de servidor QUIC não os cobria. O usuário solicitou a
dispensa temporária de recursos não usados pelo worker; a política mantém
registros separados por CVE e expiração em **02/12/2026 UTC**, sem renovação automática.

| CVE | Condição relevante | Avaliação da arquitetura do worker |
| --- | --- | --- |
| CVE-2026-121482 | Verificação CMS/PKCS#7 SignedData com CRL | Não verifica assinaturas CMS/PKCS#7 dos documentos recebidos. TLS continua verificando certificados. |
| CVE-2026-84786 | Cliente configurado com DANE/TLSA | Os clientes AWS/PostgreSQL usam TLS convencional, sem configuração DANE. |
| CVE-2026-95691 | Cliente OpenSSL QUIC | O worker não utiliza QUIC, inclusive como cliente. |
| CVE-2026-88823 | AAD incremental no AES-GCM-SIV | Não usa AES-GCM-SIV; AES-GCM de TLS é outro algoritmo. |
| CVE-2026-95692 | IV fornecido ao AES-GCM-SIV | Não usa essa operação de criptografia. |
| CVE-2026-107496 | Criptografia CMS para certificado EC fornecido | Não cifra mensagens CMS para certificados de destinatários enviados pelo cliente. |
| CVE-2026-107493 | PEM_X509_INFO_read com pilha fornecida pelo chamador | Não há uso direto dessa família nos consumidores revisados. Segundo o fornecedor, o carregamento interno de CAs pelo OpenSSL passa NULL e não aciona essa condição. |

Fontes: [OpenSSL, 10/11](https://openssl-library.org/news/secadv/20261110.txt),
[OpenSSL, 17/11](https://openssl-library.org/news/secadv/20261117.txt),
[Debian, CMS/PKCS#7](https://security-tracker.debian.org/tracker/CVE-2026-121482)
e [Debian, DANE](https://security-tracker.debian.org/tracker/CVE-2026-84786).
Os avisos OpenSSL agendam releases para 01/12; isso não garante a disponibilidade
de um pacote Debian corrigido nessa data. A expiração não depende dessa disponibilidade.

### Evidência adicional obrigatória

As sete regras exigem `optional_crypto_assessment=lambda-client-no-optional-crypto-v1`,
resultado `passed` e inventários não vazios de fontes Python e consumidores nativos,
além de todos os checks anteriores. Evidência antiga de servidor QUIC sozinha
continua bloqueando os sete achados. O relatório bruto do scanner não é alterado.

Na preparação desta alteração, a inspeção dos wheels Linux das 23 versões do
inventário encontrou 854 arquivos Python e 44 binários ELF consumidores sem
referências às APIs verificadas. Essa evidência parcial não substitui a execução
do verificador no build completo, que também inclui CPython e bibliotecas do sistema.

O verificador amplia a inspeção para as dependências Python empacotadas e para
os consumidores nativos em `/var/task`, extensões CPython e bibliotecas do sistema.
Referências a APIs QUIC, DANE, AES-GCM-SIV, CMS/PKCS#7 e PEM_X509_INFO_read exigem
nova revisão, mesmo se aparecerem apenas em documentação. As próprias bibliotecas
libssl/libcrypto ficam fora da busca de consumidores: elas **contêm** os recursos
afetados. Nenhuma regra afirma que o binário está corrigido ou que essas APIs foram removidas.

O inventário de dependências continua fixo. Os checks detectam mudanças em relação
à arquitetura revisada, mas não provam a ausência de chamadas construídas dinamicamente.
A exceção é uma avaliação limitada a este worker e a esta versão, apoiada na revisão
de uso e nos checks da imagem real. Não se aplica à API, outros pacotes/versões,
CVEs desconhecidas ou severidade CRITICAL. No ECR, exige também correspondência
com o digest construído e validado pela mesma execução.

Antes de publicar, o workflow Worker Debian deve executar o verificador ampliado
na imagem Linux real, com testes de conversão, TLS e PostgreSQL. Após merge, o deploy
repete os checks e o scan do ECR; qualquer achado sem exceção válida continua bloqueando.
Remover estas regras quando houver pacote corrigido e validar novamente runtime,
conversões e scanners. Rollback: reverter as regras/checks deste incremento;
novos deploys voltam a bloquear os sete achados, sem alteração de dados ou schema.

## Achado QUIC adicional em 23/11/2026

A [execução 45288555012](https://github.com/claston/gettdone/actions/runs/45288555012)
do PR #632 passou nos testes e no canário da imagem, mas o Trivy bloqueou
`CVE-2026-107494`, HIGH, no mesmo `libssl3t64=3.5.7-1~deb13u3`.
O [Debian](https://security-tracker.debian.org/tracker/CVE-2026-107494) ainda não
lista pacote corrigido, inclusive em Sid. A [correção upstream da série 3.5](https://github.com/openssl/openssl/commit/734c6cd7444bdbf589bfec917e47598c1b21ddd3)
trata retransmissões de `NEW_CONNECTION_ID` que podem acumular frames
`RETIRE_CONNECTION_ID` sem limite, causando falta de memória em uma conexão QUIC.

Este achado exige avaliar **cliente e servidor QUIC**. A avaliação anterior de
servidor, isoladamente, não basta. A arquitetura revisada usa TLS convencional;
o relatório `openssl-runtime.json` dessa execução confirmou as 23 dependências,
409 fontes da aplicação, 1.263 arquivos Python e 413 consumidores nativos na
avaliação ampliada, sem referências às APIs opcionais verificadas. O verificador
também confirmou TLS com validação de certificado e rejeição de UDP pelo CPython.
Essas contagens registram a execução analisada; cada novo build refaz os checks.

A nova regra exige a avaliação ampliada completa e mantém o escopo exato de
pacote, versão, runtime e severidade, com expiração no início de **02/12/2026 UTC**.
O relatório do scanner permanece intacto e a biblioteca continua vulnerável:
esta é uma avaliação temporária de não aplicabilidade ao worker, não um patch.
Ausência de qualquer evidência, introdução de cliente/servidor QUIC, mudança
do inventário, severidade CRITICAL ou digest ECR divergente continuam bloqueando.

Os testes reproduzem o bloqueio em Trivy/ECR antes da inclusão da regra e cobrem
aceitação com evidência completa, rejeição de evidência antiga/incompleta,
expiração e referências QUIC em dependências Python e nativas. A imagem Linux
deve passar novamente pelo workflow Worker Debian antes do merge. Remover esta
regra quando houver pacote corrigido; não estender automaticamente o prazo.
Rollback: reverter apenas a inclusão desta CVE na política e no gate, restabelecendo
o bloqueio de novas releases sem alterar dados ou a imagem em produção.
