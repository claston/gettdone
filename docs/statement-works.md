# Trabalhos e fechamento de extratos — OFX-61

O módulo `/works` organiza a preparação de extratos por cliente e período
flexível. Conversões, revisão e downloads existentes continuam independentes.
É uma entrega para piloto privado por proprietário, sem compartilhamento de
equipe, fechamento fiscal/contábil certificado ou classificação de receitas.

## Fluxo para teste

1. Em Clientes, cadastrar cliente e suas contas; ou usar os cadastros existentes.
2. Abrir Trabalhos e escolher cliente + período. Cliente único é preenchido;
   é possível criar um período livre na própria tela. Criar novamente para a
   mesma combinação recupera o trabalho existente, inclusive se concluído.
3. Em Contas e cobertura, **confirmar explicitamente** as contas esperadas.
   Cadastrar uma conta não a inclui automaticamente no trabalho. Excluir do
   escopo não altera seu cadastro e não gera falta de extrato daquela conta.
4. Vincular conversões existentes em Extratos e movimentações, ou usar Converter
   novos arquivos. Nesse segundo fluxo o lote é vinculado ao cliente/período e
   a revisão retorna ao trabalho. Uma falha posterior de vínculo pode ser
   retomada sem criar outro lote. A conta é confirmada individualmente.
5. Conferir titular/conta, datas declaradas no documento e revisão. Datas dos
   lançamentos não são usadas como cobertura. Documento sem movimentos exige
   confirmação explícita e saldo identificado ou informado pelo usuário.
6. Resolver bloqueios, complementar extratos ou excluir dados daquele trabalho
   com justificativa. Revisar uma inconsistência financeira não a elimina.
7. Conferir o resumo e justificar as ressalvas **por conta/período**. O servidor
   reavalia a versão e os dados; não aceita a decisão calculada pelo navegador.
8. Concluir e baixar o recorte em CSV. OFX/Excel individuais continuam sendo o
   **arquivo completo**, com essa indicação na interface. Não há um novo OFX
   consolidado do período nesta entrega.
9. Alterações posteriores sinalizam reavaliação sem reescrever a versão
   concluída. Reabrir exige motivo; nova conclusão cria nova revisão.

O caminho inverso também funciona: após organizar conversão por cliente e
período, o atalho Abrir trabalho preenche a combinação. Todos os caminhos usam
os vínculos canônicos da OFX-24, sem copiar arquivo ou consumir cota pelo vínculo.

## Regras da conferência

- Cobertura por conta: completa, parcial, sobreposta, indeterminada ou ausente.
  União de intervalos documentais recortada pelas datas do trabalho produz
  lacunas/sobreposições. Há confirmação manual com autor/data quando o pipeline
  não oferece intervalo documental confiável.
- A conta de origem vem da evidência imutável `organization_source`, quando
  disponível. Configurar conta de destino para exportação não muda essa origem.
  Em legados sem evidência, a confirmação documental é obrigatória. Titular ou
  conta em conflito com evidência conhecida continua bloqueado.
- Processamento pendente/falho, arquivo indisponível, conta não associada,
  ausência de extrato válido no recorte, revisão pendente, extração incompleta,
  discrepância de saldo e possíveis duplicidades não resolvidas bloqueiam.
- Confiança de layout isolada não é bloqueio. Qualidade financeira e cobertura
  documental são verificações distintas.
- Cobertura incompleta/indeterminada, sobreposição ou continuidade de saldo
  não verificável permitem conclusão com ressalvas justificadas. Saldos
  conhecidos divergentes não são convertidos em ressalva.
- Duplicidade é sinalizada por conta + data + valor + descrição normalizada.
  Exclusão de documento afetado ou declaração explícita de movimentos distintos
  com evidência resolve o grupo. Mudança dos movimentos muda a identificação
  do grupo e exige nova conferência. Transferências/estornos não são tratados
  como receitas/despesas ou conciliados automaticamente.
- Recorte inclui datas inicial/final. Não soma resultados entre trabalhos;
  reutilizar uma conversão não duplica o arquivo nem cria consolidação entre
  períodos sobrepostos. O CSV identifica conversão/lançamento/conta e protege
  células textuais contra interpretação como fórmulas.
- Gravações usam versão esperada e transação única para trabalho + vínculo.
  As duas versões são verificadas quando se altera a conta de um documento.
  Um trabalho concluído precisa ser reaberto para essas mudanças.

## Persistência e retenção

Migration aditiva `20261027_01`, após `20261024_01`, cria:

- `pro_statement_works`: combinação única organização/cliente/período, estado,
  versão, escopo confirmado e anotações de trabalho.
- `pro_statement_closings`: revisões lógicas imutáveis com cliente/período,
  contas, versões e hashes dos documentos, contagens, validações e ressalvas.
- `pro_statement_work_events`: eventos e autoria de criação, alterações,
  conferências, exclusões, conclusão e reabertura.

Nenhuma tabela antiga é alterada e não há backfill. A API/worker anteriores
continuam compatíveis antes/depois da migração. SQLite local usa o mesmo DDL;
o caminho PostgreSQL é exclusivamente Alembic.

As revisões não guardam os lançamentos nem PDFs. Metadados e declarações do
usuário permanecem como dados do trabalho; incluem motivos e confirmações
manuais. Arquivos/resultados seguem a retenção existente. Após expiração, os
lançamentos não são devolvidos, a revisão perde seu link e a exportação é
bloqueada. Concluir não renova retenção nem concede novo acesso comercial.

O fingerprint compara os dados utilizados na conferência, inclusive versão
global do vínculo. Mudanças em contas fora do escopo não geram reavaliação;
reorganizar o mesmo documento pode exigir reavaliação da versão do vínculo.
Consultas reavaliam os documentos atuais; exportação de trabalho concluído exige
que ainda correspondam à versão concluída. Não há transação distribuída com
as edições do workspace: alterações concorrentes posteriores ao instante da
conferência são detectadas na próxima consulta/exportação.

## Piloto, publicação e rollback

1. Aplicar `venv\Scripts\python.exe -m alembic upgrade head` em `backend`,
   usando a configuração exclusiva do ambiente escolhido.
2. Publicar API antes do frontend. Worker não precisa mudar.
3. Habilitar `PRO_ORGANIZATION_ENABLED=true` e
   `PRO_STATEMENT_WORKS_ENABLED=true` apenas para o ambiente do piloto.
   O segundo flag é desligado por padrão em produção e habilitado em dev.
4. Validar proprietário, 401 sem sessão, 403 Free/sem guard de escrita, 404
   entre proprietários e 409 para versão antiga/conclusão desatualizada.
5. Conferir cota e jobs antes/depois de vínculos/conclusão; devem ficar iguais.
6. Observar 5xx/409, latência da avaliação, crescimento de histórico e falhas
   de exportação. Abrir um trabalho concluído após edição/expiração deve alertar
   reavaliação. Testar conversor com o flag de trabalhos desligado.

A autorização existente exige sessão PRO com permissão de lotes e isolamento
por proprietário. O flag é operacional e não implementa a matriz comercial
definitiva Pro Escritório nem colaboração; essas definições seguem OFX-20/30.

Limites do piloto: até 200 extratos por trabalho, 100 contas, lista paginada de
20 trabalhos. A avaliação é síncrona, consulta fontes atuais e pode ficar mais
lenta com muitos arquivos/revisões; não há processamento financeiro novo no
worker. Nenhum percentual de completude é usado.

Rollback preferido: desligar o flag de trabalhos e reverter frontend/API,
**mantendo as tabelas** e seus registros. Não apagar vínculos da OFX-24.
Downgrade técnico para `20261024_01` remove somente as três tabelas novas e
perde seus históricos; após uso real exige backup e plano de restauração.
Não usar esse downgrade destrutivo como rollback operacional.

## Evidência de validação

- TDD do domínio e da interface, incluindo falha inicial.
- `test_statement_work_assessment.py`: cobertura, recorte, datas extremas,
  identidade/finanças, duplicidades, transferências/estornos e ausência legítima.
- `test_statement_works_http.py`: HTTP real, idempotência, versões, falhas de
  autorização, vínculo de lote, conclusão/reabertura, CSV, ressalvas por conta,
  reutilização entre períodos, expiração e cota/fila inalteradas.
- `test_statement_work_migration.py`: DDL/constraints, downgrade preservando
  usuários/clientes, reupgrade e rejeição de identificador de schema inválido.
- `test_pro_organization_postgres.py`: PostgreSQL 16 descartável, cadeia inteira
  Alembic, uso real do serviço, conclusão/reabertura, downgrade só da nova
  migração preservando clientes/conversões, reupgrade e compatibilidade anterior.
- `scripts/smoke_statement_works.py --url http://127.0.0.1:3101`: Chromium com
  API HTTP real descartável e dados sintéticos, desktop/mobile, ressalvas,
  download, retorno da revisão e histórico imutável. Incluído no CI como suite
  `works`, com dependências backend somente nesse perfil de navegador.

`/analyze` permanece desativado (405), conforme main. A suíte completa preserva
o conversor público e relatórios atuais. Nenhuma publicação em produção faz
parte desta implementação.
