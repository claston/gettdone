# Recuperação de senha

O link **Esqueci minha senha** aparece no login público e no login PRO. A página
`/esqueci-senha.html` solicita o e-mail; `/redefinir-senha.html#token=…` permite
informar e confirmar a nova senha. A conta Google continua usando **Continuar com
Google**. A recuperação não transforma uma conta Google em conta com senha nem
dispensa a confirmação de e-mail de contas pendentes.

## Contrato e proteção

- `POST /auth/forgot-password` recebe `{"email":"pessoa@example.com"}` e retorna
  `202` com a mesma mensagem para contas elegíveis, desconhecidas, desativadas,
  Google, limitadas e falhas do provedor de e-mail.
- `POST /auth/reset-password` recebe `{"token":"…","password":"…"}`. Retorna
  `200` após a troca ou `400` para token inválido, expirado, substituído ou usado.
- As senhas mantêm a regra do cadastro: 8 a 256 caracteres e PBKDF2 com novo salt.
- Tokens aleatórios de 256 bits expiram em 30 minutos. Somente o hash SHA-256
  fica no banco. Uma nova solicitação invalida links anteriores.
- Cada conta pode solicitar no máximo um envio por minuto e cinco por 24 horas,
  inclusive tentativas de entrega que falharam. As verificações usam o banco e
  bloqueio por usuário para valer entre processos/instâncias.
- A troca, o consumo do token, a revogação das sessões e o incremento de
  `users.credential_version` acontecem na mesma transação. O bloqueio de usuário
  impede o consumo simultâneo do link. A senha e o salt anteriores são substituídos.
- Credenciais legadas anteriores são recusadas após a troca. Usuários que não
  recuperaram a senha preservam o formato e a validade de suas credenciais.
- O navegador retira o token da URL e o mantém somente em memória até o envio.
  Abrir o link não o consome. Não há login automático após a troca.
- Respostas de validação não incluem senha/token. Logs de entrega registram
  somente um identificador interno e o resultado, sem payload do provedor.
- A entrega ocorre após a resposta HTTP, para que a latência do provedor não
  revele a existência da conta. A tarefa usa o processo da API; se ele for
  interrompido durante o envio, o usuário pode solicitar outro link após um minuto.

## Configuração e implantação

1. Aplique `backend/alembic/versions/20261109_01_password_reset.py` com o runner
   habitual de migrações antes de subir o código novo. A revisão adiciona a tabela
   `password_reset_tokens`, um índice por usuário/data e a coluna inteira
   `credential_version NOT NULL DEFAULT 0`. Não há backfill manual. A aplicação
   SQLite faz a alteração aditiva na inicialização.
2. Mantenha `AUTH_PASSWORD_RESET_ENABLED=false` durante um rollout com instâncias
   antigas. Essas instâncias não verificam a versão das credenciais legadas.
   Somente libere a recuperação quando todas as instâncias da API executarem o
   código novo. Escritores antigos continuam compatíveis com o esquema aditivo.
3. Publique as páginas e `password-reset.js`. A URL padrão de destino é
   `https://www.ofxsimples.com.br/redefinir-senha.html`; configure
   `AUTH_PASSWORD_RESET_FRONTEND_URL` para outro ambiente. HTTPS é obrigatório,
   exceto em localhost. O servidor não usa o Host da requisição para montar links.
4. Reutilize o provedor de e-mail configurado em `get_contact_service` (SMTP ou
   Resend), com remetente autorizado e modo de simulação desativado em produção.
   Nenhuma credencial adicional de e-mail é necessária para este recurso.
5. Ative `AUTH_PASSWORD_RESET_ENABLED=true`. Valide com uma conta de teste
   controlada: entrega real, troca, login com nova senha, recusa da anterior,
   recusa do link reutilizado e recusa de uma sessão anterior.
6. Monitore `password_reset_delivery_failed`, `password_reset_delivery_succeeded`,
   respostas 5xx e tempos de login. A validação de credenciais legadas agora faz
   uma leitura adicional da versão no banco, sem cache para evitar revogação tardia.

O índice é criado na tabela nova, inicialmente vazia. A coluna com default
constante não exige backfill em versões atuais do PostgreSQL; o ALTER ainda
requer bloqueio breve de metadados. Use a janela e o limite de lock da operação
habitual de migração. O crescimento esperado é de até cinco linhas por usuário
solicitante/dia; não há rotina automática nova de limpeza nesta entrega.

## Rollback

Desative `AUTH_PASSWORD_RESET_ENABLED` para retornar `503` nas duas rotas e, se
necessário, reverta os links do frontend. Preserve o código de validação de
`credential_version`, a coluna e a tabela. Um rollback completo para um backend
anterior após uma troca poderia revalidar tokens legados comprometidos; faça uma
correção progressiva mantendo a revogação. O downgrade Alembic é bloqueado quando
existe um usuário com `credential_version > 0`, para impedir essa perda de
proteção. Senhas já trocadas permanecem válidas e não são restauradas. Se nenhuma
senha foi redefinida, o downgrade é permitido e preserva a coluna, a tabela e os
dados; o código anterior pode operar com esses objetos aditivos presentes.
Desative a recuperação antes do rollback. A verificação segura bloqueia escritas
em `users` durante a transação de migração para não concorrer com uma redefinição.

## Validação reproduzível

- Execute `scripts/test_backend.py` com o Python do ambiente virtual para a suíte
  completa (no Windows Codex, fora do sandbox).
- Para exercitar PostgreSQL, aponte `PASSWORD_RESET_TEST_DATABASE_URL` para um
  banco descartável **local**. O teste cria um schema aleatório, migra até a
  revisão anterior, insere usuários antigos, aplica a migração duas vezes,
  comprova escrita antiga e preservação dos dados, executa downgrade/re-upgrade
  antes de uma redefinição e verifica o bloqueio do downgrade depois dela.
  Também testa consumo concorrente entre dois serviços. Remove somente esse
  schema ao terminar. A etapa PostgreSQL da CI executa esse teste junto aos
  testes de rollback de organizações e movimentos canônicos.
- `pro`: `npm run test:run`, `npm run lint` e `npm run typecheck`. Os testes do
  HTML executam o script das páginas, incluindo confirmação divergente, token
  ausente/expirado, falha de rede, limpeza da URL e limitação de reenvio.
- O endpoint `/analyze` está desativado no código atual (`405`). Use o upload
  atual `/api/conversions/upload`, valide `/report/{analysis_id}` e um ID
  inexistente para a verificação de regressão de conversões.
