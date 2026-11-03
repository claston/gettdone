# Execução dos testes com pouca memória

Execute `scripts/test-backend.ps1` na raiz do checkout, ou `python scripts/test_backend.py` com o ambiente virtual do projeto ativado. Sem filtros, o runner solicita a suíte completa, incluindo os testes `pdf_golden` e `real_http`. Dependências opcionais continuam usando os critérios de skip dos próprios testes.

## Paralelismo e resultados

- `--workers auto` é o padrão. Reserva 512 MiB e estima 640 MiB por worker, com limite de quatro processos e da quantidade de CPUs. É uma estimativa conservadora, não um limite imposto de memória.
- No Windows, considera o menor valor entre memória física disponível e capacidade disponível de commit. No Linux, considera `MemAvailable` e, quando presentes, os limites de cgroup v2. Sem informação confiável, escolhe um processo.
- Quando seleciona um processo, usa `pytest -n 0`, evitando criar também um controlador xdist. Uma máquina com aproximadamente 790 MiB livres segue esse caminho.
- `--workers 2` ou `PYTEST_WORKERS=2` permite uma decisão explícita. O runner avisa se a escolha exceder seu orçamento estimado; não inicia processos adicionais durante a execução.
- Um lock do sistema operacional impede duas execuções pelo runner no mesmo checkout. Ele não controla comandos pytest diretos nem outros checkouts.
- Cada execução preserva `junit.xml` e `run.json` em `tmp/test-runs/<UTC-id>/`. O JSON registra comando, workers, memória disponível, duração e código de saída. O XML permite comparar tempos por caso.
- O diretório temporário exclusivo fica no temp local do usuário e é removido em saídas normais, falhas e interrupções tratadas. Um encerramento forçado do processo pode impedir essa limpeza.

No Codex Windows, execute também o runner com `sandbox_permissions: require_escalated`, conforme `AGENTS.md`. Para rodar só um arquivo, passe seu caminho após os argumentos do runner; para medir uma regressão, mantenha iguais a seleção, os recursos externos e a quantidade de processos.

## Trabalho repetido eliminado

Os testes HTTP reutilizam um listener TCP por processo. Cada caso mantém seu próprio ciclo de inicialização e encerramento do FastAPI, overrides, armazenamento e clientes. A infraestrutura aguarda tarefas de background antes de encerrar o ciclo e liberar o próximo caso. Cookies pertencem aos clientes, e conexões remanescentes são fechadas na troca de aplicação.

O cliente de teste continua sendo HTTPX sobre TCP real. Seu transporte aceita somente HTTP em `127.0.0.1`, rejeita HTTPS e destinos externos e ignora proxies do ambiente. Isso evita carregar certificados TLS em cada cliente para requisições locais sem TLS. Nenhum cliente de produção é alterado.

As instâncias SQLite de teste em memória recebem uma cópia independente do banco inicializado uma vez por processo, assim como já acontecia com os arquivos SQLite temporários. Bancos existentes e PostgreSQL continuam percorrendo a inicialização habitual; dados de um teste não entram no template.

As fixtures canônicas de PostgreSQL continuam criando schemas novos e executando todas as migrations reais. A preparação chama Alembic no mesmo interpretador, preserva o logging do pytest e restaura as variáveis de ambiente depois. Testes explícitos de CLI, upgrade e rollback continuam exercitando subprocessos. A preparação aceita apenas os bancos locais descartáveis já autorizados pelos testes.

## Limites da comparação histórica

O resultado de aproximadamente 40 segundos foi obtido com cerca de 1.260 testes e quatro workers. Em novembro, a suíte já tinha 4.051 casos; execuções seriais, preparação repetida e pressão de memória elevaram o tempo. Não se deve comparar diretamente os dois tempos como se a carga e o hardware disponível fossem iguais.

A otimização mantém as asserções e a seleção dos testes existentes. Não altera timeouts dos clientes, não remove testes lentos e não modifica código, migrations ou configurações de produção.

## Medições locais em 3 de novembro de 2026

Máquina Windows com aproximadamente 8 GB de RAM total. O runner encontrou apenas 778 MiB disponíveis ao iniciar a execução completa otimizada. Ambas as execuções completas abaixo foram seriais e sem habilitar as integrações opcionais de PostgreSQL.

| Execução | Resultado | Tempo do pytest |
| --- | --- | --- |
| Antes da alteração | 3.993 aprovados, 1 falha por timeout HTTP, 55 skips, 2 xfails | 1.141,54 s |
| Depois da alteração | 4.025 aprovados, 55 skips, 2 xfails | 358,11 s |

Redução observada de 68,6%, ou aproximadamente 3,2 vezes mais rápido. A execução otimizada adicionou 31 casos de regressão de infraestrutura e aprovou o caso que antes falhou; não excluiu casos existentes. O tempo total do processo, incluindo inicialização e término do pytest, foi 360,958 s. O XML ficou em `tmp/test-runs/20261103T122312Z-1d3f787b/junit.xml`.

Na amostra dos mesmos nove casos de `test_oct28_new_statement_failures_http.py`, a soma dos tempos por caso caiu de 11,218 s para 3,145 s. Essa comparação usa os mesmos identificadores de teste nos dois XMLs, sem misturar os testes novos com a amostra original.

Os valores são observações desta máquina, não garantias de tempo para qualquer hardware. A medição completa ainda está acima dos 40 segundos históricos; a suíte atual é mais de três vezes maior e a medição usa um único processo. Compare futuras execuções pelos relatórios retidos e registre separadamente a habilitação das integrações PostgreSQL.

Validações complementares: 82 testes de PostgreSQL e infraestrutura aprovados em 141,77 s, incluindo upgrade/downgrade/reupgrade, processos concorrentes e contratos HTTP; 36 testes pelo wrapper PowerShell em modo automático aprovados em 3,19 s, escolhendo um processo com 685 MiB livres; e 45 regressões de HTTP/SQLite/runner aprovadas com dois workers em 17,04 s. A última execução usa um override intencional para verificar isolamento entre workers, não como recomendação para uma máquina com pouca memória.
