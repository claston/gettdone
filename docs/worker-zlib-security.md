# Atualização do zlib da imagem do worker

O scanner das PRs #554 e #555 identificou `CVE-2026-76844` no pacote
`zlib1g 1:1.3.dfsg+really1.3.1-1+b1` da base distroless Debian 13. A falha
permite uma escrita fora do buffer após um erro de escrita e uma nova chamada
`gzwrite`, precedida de `gzclearerr` nas versões antigas.

## Pacote e compatibilidade

Os dois Dockerfiles do worker instalam somente o pacote oficial Debian
`zlib1g 1:1.3.dfsg+really1.3.2-4`, que contém a correção upstream
`df84af25dc1942490e1d1c899a07619152a46148`. Não se trata da versão upstream
1.3.2 sem patches, que permanece vulnerável.

O pacote vem do espelho oficial `deb.debian.org`, com índices assinados
verificados pelo APT. Assinatura, validade, integridade e autenticação
continuam obrigatórias; falhas ao atualizar índices interrompem o build.
Não há atualização geral do sistema nem habilitação permanente de Sid. As
bases Python e distroless continuam fixadas pelos mesmos digests. A versão
exata é obrigatória: se sair do espelho, o build falha em vez de selecionar
automaticamente uma biblioteca diferente.

A biblioteca mantém o SONAME `libz.so.1`. A imagem de produção é Linux/amd64;
este procedimento não declara suporte a outras arquiteturas. O binário antigo
é removido, e os arquivos do pacote novo, licença, status e checksums oficiais
são copiados integralmente para o runtime. O processo continua como usuário
65532, sem shell ou ferramentas de compilação na imagem final.

Não há alterações de API, extração financeira, dependências Python, banco ou
configuração de produção. A migração para um pacote Trixie corrigido deverá
substituir este uso pontual de pacote Sid quando houver publicação oficial.

## Evidência executável

`scripts/check_worker_zlib.py` verifica:

- exatamente um registro do pacote e a versão esperada;
- checksum do binário conforme o pacote autenticado pelo APT;
- ausência do binário anterior e destino correto do link `libz.so.1`;
- versão da biblioteca efetivamente carregada pelo Python;
- escrita em `/dev/full`, erro `Z_ERRNO`, limpeza do erro e nova escrita curta.

A regressão roda em subprocesso, com limite de tempo. A CI exige que a imagem
Python original reproduza SIGSEGV depois do erro de escrita, e que a imagem
atualizada aceite a nova escrita sem falha de memória. Os JSONs antes/depois
são anexados ao mesmo artefato dos testes de runtime, SBOM e scanner.

Os testes de conversão CSV/XLSX/OFX/PDF, PDFium, cliente Lambda e PostgreSQL
continuam obrigatórios. A suíte completa roda na CI para evitar OOM local.

## Scanner e limite conhecido

Em 5 de novembro de 2026, o Debian Security Tracker marca o pacote Sid acima
como corrigido, mas ainda lista Trixie sem versão corrigida. O detector Debian
do Trivy 0.76.1 consulta a distribuição da imagem e, quando `FixedVersion`
está vazio, reporta o alerta sem comparar versões. Portanto, este pacote
corrigido em Debian 13 pode continuar bloqueado pela base de vulnerabilidades.

Esta alteração não ignora esse resultado: nenhum CVE é suprimido, nenhum
inventário é ocultado, e HIGH/CRITICAL continuam bloqueando CI e deploy.
A aprovação depende do resultado real da esteira; um teste de regressão
aprovado não substitui a aprovação do scanner.

## Rollback e operação

Esta PR não faz merge nem deploy. Se a atualização apresentar incompatibilidade,
reverter o commit restaura a construção anterior, mas também restaura o pacote
vulnerável: esse rollback não constitui uma versão segura para publicação.
Manter o bloqueio de release até existir uma imagem aprovada.

Fontes:

- [Debian Security Tracker](https://security-tracker.debian.org/tracker/CVE-2026-76844)
- [Pacote oficial corrigido](https://packages.debian.org/sid/zlib1g)
- [Correção upstream](https://github.com/madler/zlib/commit/df84af25dc1942490e1d1c899a07619152a46148)
- [Detector Debian do Trivy 0.76.1](https://github.com/aquasecurity/trivy/blob/v0.76.1/pkg/detector/ospkg/debian/debian.go)
