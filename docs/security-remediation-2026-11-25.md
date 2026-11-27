# Correções de segurança — 25/11/2026

## Comportamento

- O token determinístico antigo deixa de ser aceito, mesmo com assinatura correta e versão de credencial atual. `/auth/session/migrate` responde `410`: é necessário entrar novamente. O navegador apaga os valores antigos sem enviá-los para migração.
- `/auth/login` e cadastros já verificados mantêm o campo `user_token`, agora com um token de sessão revogável e expiração padrão de 15 minutos. Esses endpoints preservam o comportamento de resposta sem cookies; o cliente envia Bearer ao fazer logout e realiza novo login quando o token expira. O navegador continua usando `/auth/session/login` e refresh por cookies HttpOnly. Logout revoga a família da sessão apresentada. Sessões por cookie emitidas antes do deploy continuam válidas até expiração ou revogação.
- XLSX passa por validação antes do openpyxl: no máximo 2.048 membros ZIP, 16 MiB expandidos no total, 8 MiB por membro, 50.000 linhas, 256 colunas e 500.000 posições na grade de cada aba. Declarações DTD/ENTITY são recusadas. Coordenadas são conferidas independentemente da dimensão declarada; dimensões pequenas incorretas não podem ocultar movimentos. A leitura é incremental e verifica o tempo durante o processamento. O limite de 15 segundos é cooperativo, não um encerramento forçado de processo.
- O texto decodificado é limitado a 32.767 caracteres por célula e 8 Mi caracteres por leitura; formatos numéricos têm até 256 caracteres. Isso limita também a expansão de strings compartilhadas e máscaras de zeros. A classificação lê somente as primeiras 20 linhas após validar o arquivo. Valores, sinais, datas e formatação explícita de números de documento permanecem preservados.
- Retornos de autenticação aceitam somente caminhos locais. Barras invertidas, caracteres de controle e destinos com outra origem são recusados no navegador e no OAuth do backend.
- Respostas de autenticação, arquivos financeiros e APIs privadas recebem `Cache-Control: no-store` quando ainda não há essa proteção.
- A imagem da API executa com UID/GID 10001. Os diretórios padrão `backend/tmp` e `backend/data` são graváveis; volumes externos configurados precisam permitir acesso a esse UID.

## Limites públicos

Os limites ficam no banco, compartilhados entre instâncias. Apagar cookies, trocar fingerprints ou reiniciar a API não os renova. Eles não criam identidades compartilhadas por IP: usuários atrás do mesmo NAT continuam sem acesso aos arquivos uns dos outros.

| Operação | Por rede | Global |
| --- | --- | --- |
| Criação de sessão anônima | 10 por minuto | 1.000 por minuto |
| Processamento anônimo | Cota anônima configurada por janela de cota | 1.000 solicitações por hora |

`PUBLIC_CONVERSION_NETWORK_LIMIT` permite ajustar o limite por rede; `PUBLIC_CONVERSION_HOURLY_LIMIT` ajusta o teto global. Ambos devem ser inteiros positivos. O limite de rede considera IPv4 ou um prefixo IPv6 /64. A janela é fixa, e o `Retry-After` informa o tempo restante. Tentativas de processamento, inclusive arquivos inválidos, consomem o orçamento; lotes consomem uma unidade por arquivo. Repetições do mesmo lote, com o mesmo proprietário e chave de idempotência, não consomem novamente. Contas autenticadas continuam sujeitas às cotas de seu plano.

Os identificadores de rede são HMACs, e os registros expirados são removidos nas próximas solicitações. Todas as instâncias precisam do mesmo segredo e banco. A aplicação usa `request.client.host`; a infraestrutura deve confiar em cabeçalhos de proxy somente quando recebidos dos proxies conhecidos. Configurar confiança irrestrita em um servidor acessível diretamente permite falsificar o endereço do cliente.

O limite por rede também alcança usuários legítimos que compartilham NAT. A mensagem informa esse alcance e oferece entrar na conta ou aguardar; o produto já possui ambas as opções. Monitorar `429/public_request_limit`, conversões anônimas e chamados de suporte após o deploy.

## Migração e revogação

1. Aplicar Alembic até `20261125_01` antes de iniciar a nova API. A migração adiciona `public_request_budgets`, `public_budget_operations` e índices; não transforma dados existentes. A versão anterior da aplicação continua compatível com o banco ampliado.
2. Configurar `DATABASE_URL` e `DATABASE_SCHEMA` por um ambiente seguro do operador, sem colocar a conexão na linha de comando, no PR ou em logs.
3. Simular a revogação: `backend/venv/Scripts/python.exe backend/scripts/revoke_legacy_tokens.py`. O resultado contém somente contagens.
4. Aplicar: `backend/venv/Scripts/python.exe backend/scripts/revoke_legacy_tokens.py --apply`. No Linux, usar o Python do ambiente virtual correspondente. O comando eleva `users.credential_version` a pelo menos `2026112501` em uma transação. Ele não altera senhas, permissões nem `user_sessions`. A versão anterior do backend já verifica esse campo, portanto os tokens anteriores são invalidados também antes do deploy. Reexecutar é idempotente.
5. Publicar API, frontend e worker construídos com este commit. Enquanto a API antiga estiver executando, novos logins ainda podem emitir o formato antigo com a versão elevada. A rejeição definitiva de todo esse formato depende do deploy da API corrigida em todas as instâncias.
6. Validar login, refresh, logout, recusa do token antigo, conversão, download e recusa de acesso por outro proprietário. Confirmar o `Retry-After` após exceder o limite público. Usar documentos e contas sintéticos.

O update de credenciais faz uma passagem pela tabela de usuários e bloqueia as linhas atualizadas até o commit; o tempo depende do volume. Não há alteração destrutiva de esquema. O teste em PostgreSQL cobre uma aplicação anterior escrevendo antes/depois da migração, concorrência entre instâncias, simulação, execução repetida, preservação de cookies e downgrade/re-upgrade.

O workflow existente `deploy-render-staging.yml` também permite executar somente essa manutenção com `revoke_legacy_tokens=true`. Ele usa o mesmo `DATABASE_URL`/`DATABASE_SCHEMA` do fluxo de publicação da API, simula, aplica e verifica a revogação. Nesse modo, os jobs de migração e deploy são ignorados; nenhuma imagem é republicada. A execução usa as permissões e proteções já existentes do ambiente GitHub `staging`, sem copiar a conexão para a máquina do operador. O nome desse ambiente é herdado do fluxo de publicação e deve ser considerado ao selecionar o alvo.

## Rollback

O downgrade preserva as tabelas e os orçamentos, evitando que um rollback/re-upgrade renove o limite de um atacante. **Não reduzir `credential_version`**: isso revalidaria credenciais revogadas. A revogação é intencionalmente irreversível; o usuário recupera acesso fazendo login.

Voltar à imagem anterior reintroduz a aceitação do formato antigo e as falhas de processamento. Preferir uma correção incremental; se rollback da aplicação for indispensável, preservar o banco, manter as versões de credenciais elevadas e restringir temporariamente os caminhos vulneráveis na infraestrutura. Não restaurar o banco inteiro para desfazer esta migração.

## Dependências e validação

API: python-multipart 0.0.31, Pillow 13.0.0, urllib3 2.8.0, AnyIO 4.16.0, certifi 2026.11.20 e idna 3.20. Worker: mesmas versões de Pillow e urllib3. A checagem PyPI das 40 dependências instaladas da API não encontrou avisos conhecidos nessas versões. Isso não equivale a uma análise da imagem publicada ou de suas bibliotecas de sistema.

Referências do mantenedor para o problema de multipart: [cabeçalhos multipart](https://github.com/Kludex/python-multipart/security/advisories/GHSA-pp6c-gr5w-3c5g) e [query string](https://github.com/Kludex/python-multipart/security/advisories/GHSA-5rvq-cxj2-64vf). Os resultados completos de testes, HTTP e lint acompanham o PR.

Mensagens revisadas: sessão antiga encerrada, limite por rede, excesso de solicitações e rejeição de planilha. Elas explicam a falha sem expor tokens ou dados financeiros. Não há alterações nas regras de validação financeira, reconciliação ou permissões de acesso aos relatórios.
