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
- escrita transparente em `/dev/full`, erro `Z_ERRNO`, limpeza do erro e nova
  escrita curta, com uma página de memória protegida após o buffer de entrada.

A regressão roda em subprocesso, com limite de tempo. A CI exige que a imagem
Python original reproduza SIGSEGV depois do erro de escrita, e que a imagem
atualizada aceite a nova escrita sem falha de memória. O modo transparente
garante que o erro aconteça com entrada ainda pendente, sem depender de quanto
o compressor já consumiu. Falhas desse teste interrompem a etapa, sem pipeline
de shell que possa ocultar seu código de saída. Os JSONs antes/depois
são anexados ao mesmo artefato dos testes de runtime, SBOM e scanner.

Os testes de conversão CSV/XLSX/OFX/PDF, PDFium, cliente Lambda e PostgreSQL
continuam obrigatórios. A suíte completa roda na CI para evitar OOM local.

## Scanner e limite conhecido

Em 5 de novembro de 2026, o Debian Security Tracker marca o pacote Sid acima
como corrigido, mas ainda lista Trixie sem versão corrigida. O detector Debian
do Trivy 0.76.1 consulta a distribuição da imagem e, quando `FixedVersion`
está vazio, reporta o alerta sem comparar versões. Portanto, este pacote
corrigido em Debian 13 pode continuar bloqueado pela base de vulnerabilidades.

## Exceção temporária e verificável

`.github/worker-vulnerability-exceptions.json` registra uma única exceção:
`CVE-2026-76844`, pacote `zlib1g`, versão **exata**
`1:1.3.dfsg+really1.3.2-4`, severidade `HIGH`. Ela vence no início de
**6 de dezembro de 2026, às 00:00 UTC**. Não há renovação automática.
A justificativa e as referências à correção acompanham a política.

`scripts/check_worker_vulnerabilities.py` aplica essa mesma política ao Trivy
e ao ECR. A exceção só é aplicada quando o teste da imagem no mesmo build
comprova o inventário autêntico, a biblioteca carregada `1.3.2` e a regressão
de escrita aprovada em `artifacts/zlib-after.json`. O controle negativo com a
biblioteca antiga continua obrigatório. No Trivy, a exceção só vale para
pacote de sistema Debian na imagem Debian 13, nunca para dependências Python.

No Trivy, `--exit-code 0` coleta os resultados sem interromper por achados;
erros de execução do scanner ainda interrompem a etapa. Em seguida, a
verificação de cobertura do inventário e o avaliador da política são
obrigatórios. O avaliador retorna código 1 se restar qualquer HIGH/CRITICAL
fora da exceção. Não se usa `--ignore-unfixed`, `.trivyignore` genérico ou
`continue-on-error`. O relatório bruto `vulnerabilities.json` continua
incluindo o achado aceito, e `trivy-security-decision.json` explica a decisão.

O ECR não consome arquivos de ignore do Trivy. O workflow de deploy preserva
o relatório completo em `ecr-vulnerabilities.json` e aplica a política antes
de criar ou executar alterações no stack. A decisão é salva em
`ecr-security-decision.json`, no resumo da execução e em artefato separado.
O scan precisa estar completo, corresponder ao digest solicitado e ter
contagens consistentes com os detalhes. Respostas incompletas, formato
Enhanced ou paginação pendente bloqueiam a execução, exigindo adaptação
explícita; não são interpretadas como ausência de vulnerabilidades.

A exceção no ECR exige também que o digest seja o publicado a partir da
imagem construída e testada nessa execução. A opção histórica
`pr488-debian-canary` não pode usar a exceção, pois não produz essa evidência.
Nenhuma configuração de supressão é criada na AWS: apenas a decisão do
workflow considera a exceção, mantendo o resultado do scanner visível.

Versões antigas ou diferentes, outra CVE, severidade alterada para CRITICAL,
prova ausente/reprovada ou prazo vencido continuam bloqueando. Quando Trixie
publicar uma correção reconhecida pelos scanners, atualizar o pacote, remover
a exceção e validar novamente. A exceção não pode ser usada como evidência de
que a imagem antiga de produção foi corrigida.

## Rollback e operação

Esta PR não faz merge nem deploy. Se a atualização apresentar incompatibilidade,
reverter o commit restaura a construção anterior, mas também restaura o pacote
vulnerável: esse rollback não constitui uma versão segura para publicação.
Remover somente a entrada da política restaura o bloqueio do alerta sem
reintroduzir a biblioteca vulnerável. O deploy continua manual, restrito a
`main` e à confirmação do ambiente `production-worker`. Esta alteração deve
ser revisada na PR; a implantação fica para uma janela autorizada pelo usuário.

Fontes:

- [Debian Security Tracker](https://security-tracker.debian.org/tracker/CVE-2026-76844)
- [Pacote oficial corrigido](https://packages.debian.org/sid/zlib1g)
- [Correção upstream](https://github.com/madler/zlib/commit/df84af25dc1942490e1d1c899a07619152a46148)
- [Detector Debian do Trivy 0.76.1](https://github.com/aquasecurity/trivy/blob/v0.76.1/pkg/detector/ospkg/debian/debian.go)
