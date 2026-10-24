# Tempo e cobertura do gate PRO

O check obrigatório continua se chamando `pro-quality`. Ele só aprova quando
`pro-checks` e todas as execuções de `pro-browser` terminam com sucesso. O deploy
do worker continua exigindo esse check no CI do commit exato de `main`.

## Organização

| Execução | Validações |
| --- | --- |
| `pro-checks` | Todos os testes Vitest, ESLint e TypeScript. |
| `pro-browser (workspace)` | Build de produção, workspace com 12 documentos e 541 linhas, resposta de confirmação atrasada em 6 segundos, navegação após inatividade. |
| `pro-browser (pdf)` | Build de produção, sincronização PDF/linhas, marcações manuais, navegação, relatórios financeiros e exportação. |
| `pro-quality` | Confirma sucesso dos testes estáticos e das duas execuções de navegador; falha se alguma etapa falhar, for cancelada ou pulada. |

As execuções usam runners separados. Dentro de cada runner, os dois testes de
navegador continuam sequenciais para preservar a medição de desempenho. Não há
alteração nos quatro scripts existentes, nos cenários ou nos limites de tempo.
As duas execuções compilam o mesmo commit e lockfile em modo de produção, com
`NEXT_PUBLIC_API_URL=/`; cada navegador continua com seu próprio servidor Next.

`scripts/run_pro_browser_suite.py` aguarda o servidor por até 30 segundos, executa
os dois cenários mesmo se o primeiro falhar e retorna falha se qualquer um deles
não passar. O resultado e a duração de cada cenário ficam em
`tmp/pro-browser-suites/suite-*.json`. Logs, screenshots e HTML de diagnóstico
continuam nos artefatos, agora separados em `pro-workspace-acceptance-workspace`
e `pro-workspace-acceptance-pdf`.

## Preparação do navegador

`scripts/requirements-pro-browser.txt` contém somente Playwright e as três
dependências das fixtures/da geometria PDF: pytest, pypdf e pypdfium2. Um teste
exige que as três versões compartilhadas sejam iguais às do backend. Playwright
foi fixado em `1.64.1`, a versão usada pelo CI da base; a preparação não instala
clientes de AWS, banco de dados, API ou OCR que esses testes não utilizam.

O cache pip tem chave baseada nessa lista. O navegador instalado é apenas o
Chromium headless shell que os scripts já usavam (`launch(headless=True)` sem
`channel`). `--only-shell` evita baixar o Chromium de interface gráfica, conforme
a [documentação do Playwright](https://playwright.dev/python/docs/browsers#chromium-headless-shell).
As bibliotecas do sistema continuam sendo instaladas com `install-deps chromium`.
Python, pacotes do sistema e navegador aparecem em etapas separadas para medir
exatamente a origem de um atraso.

A lista de espelhos já fornecida pelo runner passa a priorizar
`https://archive.ubuntu.com/ubuntu/`; Azure e security continuam como fallbacks.
Somente as prioridades 1 e 2 dos dois endereços conhecidos são trocadas. As
assinaturas, versões e dependências exigidas pelo APT são preservadas. O formato
é o do [configurador oficial do runner](https://github.com/actions/runner-images/blob/main/images/ubuntu/scripts/build/configure-apt-sources.sh).

O gate agregado usa `always()` e verifica explicitamente os resultados de todas
as dependências: um job pulado pode aparecer como aprovado nas regras do GitHub,
por isso não se pode depender apenas do skip implícito do job agregador. Veja a
[documentação de condições do GitHub](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions).

## Medição e limites

Na base `6501f758`, o [CI 40055026066](https://github.com/claston/gettdone/actions/runs/40055026066)
levou **138 segundos** no `pro-quality`: 33 segundos para preparar Python/navegador
e 54 segundos para os testes de navegador. Na execução anterior
[40049808465](https://github.com/claston/gettdone/actions/runs/40049808465), o total
foi **187 segundos**, com 56 segundos de preparação e 67 de aceitação.

A comparação da versão nova deve medir desde o início do primeiro job PRO até
o término de `pro-quality`, incluindo filas entre jobs. A duração de poucos
segundos do agregador isolado não representa o tempo total do gate. Tempos de
fila, download e runner variam; cache frio deve ser identificado na medição.

No primeiro push da otimização, o conjunto PRO terminou em **108 segundos**
([CI 40057404489](https://github.com/claston/gettdone/actions/runs/40057404489)),
22% abaixo dos 138 segundos da base. Outra execução levou 463 segundos: o
espelho Azure baixou 25,9 MB em 6min21s, a 68,1 kB/s; os cenários passaram.
Esse atraso motivou a preferência do arquivo oficial HTTPS, sem retirar nenhum
pacote ou teste. O problema do espelho também está registrado no
[repositório dos runners](https://github.com/actions/runner-images/issues/12949).

A paralelização troca tempo de espera por mais runners simultâneos, com alguma
repetição de instalação e build. Não há teste ignorado por caminho alterado,
resultado de teste em cache ou afrouxamento de limites. As validações do backend,
golden PDFs e comparação com a versão base permanecem no mesmo workflow.

Na validação completa apareceu também uma dependência de ordem nos testes SSE:
clientes sem lifespan compartilhavam o controlador de capacidade do processo.
O módulo agora isola o lifespan por teste e exige `active_count == 0` após a
liberação real do worker, com espera limitada a dois segundos. As asserções dos
endpoints e o limite de produção não foram alterados; um vazamento de slot faz
o teste falhar em seu próprio teardown, em vez de bloquear o teste seguinte.

Para reverter, restaurar a execução sequencial anterior em `ci.yml`; o nome
obrigatório `pro-quality` e a proteção de publicação do worker são preservados.
Nenhuma alteração de aplicação, banco de dados ou produção é necessária.
