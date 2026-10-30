# Pesquisa de sucesso da conversão (OFX-63)

O conversor mostra a pergunta “Na sua avaliação, a conversão foi realizada com
sucesso?” abaixo dos downloads, com **Sim** e **Não**. A pesquisa é opcional e
não condiciona a exportação. Uma falha ao salvar permite tentar novamente.
A resposta pode ser corrigida e é recuperada ao restaurar a página ou voltar
a um resultado do lote. O seletor de resultados só aparece com dois ou mais
arquivos. Com um arquivo, a mensagem final direciona à prévia e ao download.

## API e armazenamento

`PUT /conversion-feedback/{processing_id}` usa o cookie de sessão anônima,
cookie de acesso ou Bearer já existente. Valida o acesso à conversão pelos
mesmos serviços de relatório usados nas exportações síncronas e assíncronas.
Identidade ausente retorna 401, outro proprietário 403, conversão ausente ou
expirada 404 e resposta inválida 400, conforme o tratamento da aplicação.

- `{"answer": null}` registra a apresentação da pesquisa e recupera a resposta.
- `{"answer": "yes"}` ou `{"answer": "no"}` grava ou corrige a avaliação.
- Repetir a requisição não acrescenta votos. A apresentação não apaga respostas.

`conversion_feedback` guarda apenas ID da conversão, tipo/ID da identidade,
primeira apresentação, resposta e data da resposta. Não copia arquivos,
transações, nomes ou e-mails. A chave composta impede duplicação por
conversão e identidade, inclusive entre instâncias do servidor.

## Indicadores

O painel expõe `conversion_feedback` no `GET /admin/dashboard`, respeitando
o período em America/Sao_Paulo e o filtro de identidades. A coorte considera
a **primeira apresentação da pesquisa**, não a data da resposta. Avaliações
tardias atualizam essa mesma coorte.

Os cartões mostram pessoas distintas em cada categoria e, abaixo, conversões.
A mesma identidade pode constar em categorias diferentes se avaliar arquivos
diferentes. Por isso, somar pessoas dos três cartões não representa um total
de pessoas únicas. Identidades anônimas representam navegadores. Conversões
que nunca receberam a pesquisa, inclusive o histórico anterior, não são
contadas como sem resposta. A percepção do usuário não altera o resultado das
validações financeiras automáticas.

## Implantação e rollback

1. Antes da nova aplicação, executar `venv\Scripts\python.exe -m alembic upgrade head`
   em `backend`, com o ambiente de banco de produção já configurado. A revisão
   `20261029_01` cria somente uma tabela e um índice, sem alterar dados ou
   tabelas existentes; versões antigas continuam compatíveis.
2. Publicar backend e frontend juntos. Os assets do conversor e administrador
   têm novas versões para renovar o cache. Não há backfill necessário.
3. Verificar uma conversão, uma apresentação sem resposta, uma resposta e
   uma correção. Conferir os três cartões no painel com os mesmos filtros.
   Acompanhar erros HTTP 5xx em `/conversion-feedback/` e falhas do painel nos
   logs existentes; conferir ausência de crescimento inesperado de linhas
   para o mesmo ID de conversão e identidade.
4. Em rollback, reverter os binários/assets e manter a tabela. O downgrade da
   migração é intencionalmente vazio para preservar respostas. Reaplicar o
   upgrade é seguro (`IF NOT EXISTS`). Não é necessário apagar dados.

A criação ocorre numa tabela nova e vazia, sem varredura/backfill ou bloqueio
de tabelas de conversões; indisponibilidade esperada: nenhuma. A coleta depende
do sucesso da requisição de apresentação; falhas de rede não são impressões
confirmadas e não entram na coorte.

## Validação

`tests/test_conversion_feedback.py` cobre persistência, repetição, correção,
coorte e filtro. `tests/test_conversion_feedback_http.py` executa conversão
fictícia, pesquisa, autorização e exportação num servidor HTTP real, incluindo
o caminho assíncrono. `/analyze` permanece desativado (405); o caminho atual
validado é `/api/conversions/upload`, seguido de `/report/{id}`.

`tests/test_conversion_feedback_migration.py` testa a migração em schema
PostgreSQL local descartável com `ADMIN_METRICS_TEST_DATABASE_URL`, incluindo
upgrade repetido, escrita antiga, rollback e preservação das respostas.
`scripts/smoke_conversion_feedback.py` valida navegador em 1440 e 390 px,
arquivo único/lote, OFX/Excel, erro/reenvio, restauração, painel e logout.
As capturas sintéticas ficam em `tmp/ofx63-browser/`.
