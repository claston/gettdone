# Gestão fiscal na Central Financeira PRO

Implementação inicial, exclusiva para assinantes PRO ativos e movimentos canônicos
autorizados. As flags começam desligadas. O Free, seus formatos de upload e a
extração bancária mantêm seus contratos.

## Três funções independentes

- **Notas e conciliação:** importa uma nota XML/PDF, identifica emitente e
  destinatário, apresenta correspondências e permite confirmar/desfazer vínculos
  com pagamentos ou recebimentos. Aceita alocações parciais, uma nota em vários
  movimentos e várias notas em um movimento, sem ultrapassar os saldos.
- **Gestão Tributária:** classifica movimentos como DAS, DARF a identificar, ISS,
  ICMS e outros tributos. Competência e código da receita são opcionais. Uma guia
  não é tratada como nota; impostos destacados na nota não são pagamentos bancários.
- **Faturamento estimado:** mostra separadamente vendas documentadas por emissão,
  recebimentos operacionais por data bancária, entradas pendentes e outras entradas.
  Não soma notas e recebimentos, não anualiza e não estima a receita total de
  contrapartes identificadas em um extrato. O recorte é a empresa cadastrada.

Transferências, empréstimos, aportes, aplicações e estornos classificados ficam
fora dos recebimentos operacionais. Notas canceladas, substituídas, duplicadas,
devoluções e operações sem natureza de venda ficam fora das vendas documentadas.
Sobreposição possível entre extratos, moedas diferentes de BRL e limites de
consulta impedem apresentar uma soma bancária como conferida. Ausência de dados
não comprova faturamento zero; a cobertura é sempre informada como desconhecida.

## Retorno durante a importação

Ao selecionar o arquivo, o PRO mostra o nome e o estado do envio. A confirmação
de recebimento só aparece depois do upload e da confirmação pela API; ela é
distinta de “Nota importada”, que depende da conclusão da leitura. Ao concluir,
“Ver nota importada” seleciona o documento disponível na lista. O estado do
último arquivo também aparece ao voltar à tela, sem listar todas as conclusões
anteriores. Um arquivo já recebido não é reenviado; uma tentativa anterior com
falha não é apresentada como sucesso.

O seletor permanece montado ao retornar da janela de arquivos e durante o
envio. Atualizações por foco e consultas periódicas aguardam essa interação;
a expiração do acesso continua sendo verificada. Cancelar a seleção libera a
atualização normal. Envios sem resposta são interrompidos após dois minutos,
orientando conferir a lista antes de repetir, pois o recebimento pode ter sido
confirmado sem que a resposta chegasse ao navegador.

As notas acessíveis pertencem à empresa, independentemente do mês selecionado;
o recorte mensal se aplica aos totais e aos movimentos bancários. Por exemplo,
o XML sintético do arroz, emitido em 10/03/2027, pode ser conferido na lista em
outro mês sem ser incluído nas vendas documentadas desse outro período.

## Leitura e revisão

Suporte inicial: NF-e XML 4.00 e NFS-e do padrão nacional com DPS incorporada;
PDF textual DANFE/NFS-e com campos reconhecíveis. XML municipal arbitrário,
arquivos em lote, DPS isolada e eventos separados não são aceitos como notas.
PDF exige revisão: dados ausentes ficam vazios, evidências preservam página/linha,
campos conflitantes impedem uma leitura silenciosamente parcial. Campos corrigidos
mantêm a extração original e uma operação de auditoria com antes/depois.

O sistema lê o conteúdo apresentado, sem validar assinatura ou consultar situação
na SEFAZ. Reconhece referência de substituição da NFS-e dentro da empresa; os
demais cancelamentos/substituições exigem revisão. CFOPs de venda são uma lista
conservadora; outras operações precisam de conferência. Identificadores preservam
CNPJ alfanumérico; a normalização verifica formato, sem afirmar validade cadastral.

Referências dos leiautes: [documentação nacional NFS-e](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/documentacao-atual)
e [MOC NF-e, Anexo I](https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo=J+I+v4eN00E%3D).
Na NFS-e, a emissão usa `dhProc`; a data declarada na DPS permanece separada,
conforme o [DANFSe oficial](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc/nt-008-se-cgnfse-danfse-20260505.pdf).
Finalidades de ajuste e doações exigem revisão, sem entrar automaticamente como
novas vendas ([NT 005](https://www.gov.br/nfse/pt-br/biblioteca/documentacao-tecnica/rtc/nt-005-se-cgnfse-novo-layout-rtc.pdf)).

## Armazenamento e concorrência

Migration `20261116_01`, posterior a `20261115_01`: cria cinco tabelas e seus índices,
sem alterar movimentos existentes. `bank_movements` continua sendo a fonte dos
valores bancários. Importações/documentos/vínculos/classificações/operações têm
identidade da organização e cliente. Autorização verifica dono, assinatura,
origem canônica, permissões persistidas e expiração.

Comandos usam versões esperadas e identificador idempotente. Um lock consultivo
por organização serializa alocações; locks das origens seguem o repositório
canônico. Alterações na nota ou movimento invalidam vínculos; mudança de papel da
empresa também exige revisão. Alocações inválidas continuam reservadas até serem
desfeitas ou eliminadas pela retenção. Vínculos nunca criam transações adicionais.

Upload direto assinado S3 privado, prefixo `CONVERSION_S3_PREFIX/pro-fiscal`, até
5 MB, 10 páginas e 100 importações ativas por organização. Digest SHA-256 é
conferido contra os bytes; reenviar a mesma identidade não renova sua retenção.
O evento SQS `pro.fiscal.requested` contém apenas o identificador da importação.
A linha `queued` funciona como outbox; manutenção redispara em até cinco minutos.
O worker usa lease de 12 minutos, até três tentativas, e processa fora dos locks.
O limite é conferido antes de ler o objeto ou executar OCR, inclusive ao retomar
uma tentativa interrompida por timeout ou falta de memória. Uma terceira tentativa
com lease ativo pode terminar normalmente; uma reserva vencida com orçamento
esgotado é encerrada como falha, sem novo processamento.

Substituições de NFS-e são resolvidas nos dois sentidos, independentemente da
ordem de processamento dos arquivos. A chegada da original após a substituta
também a exclui das vendas documentadas. Cadeias de substituições preservam os
dados extraídos, registram as mudanças no histórico e invalidam vínculos antigos.

Retenção máxima deste piloto: **24 horas**, limitada também por
`ANALYSIS_TTL_SECONDS` e `CONVERSION_JOB_ACTIVE_TTL_SECONDS`. Não é arquivo fiscal
permanente. Fontes expiram no prazo original; metadados e operações são apagados
pela manutenção. Classificações acompanham a retenção dos movimentos canônicos.
Expiração/revogação nunca é contornada por um vínculo. Telas limpam dados ao
mudar de escopo, falhar a consulta ou vencer o prazo, e revalidam ao retomar foco.

Consultas calculam o recorte mensal inteiro no servidor, até 10.000 movimentos;
a tela pagina em 50. CSV inclui movimentos do recorte e notas/vínculos ainda
acessíveis, com uma coluna que explicita o escopo. Exportação recusa resultados
truncados e conserva o estado de revisão. O filtro de conta afeta movimentos;
notas pertencem à empresa.

## Ativação e rollback

### Importação recebida que não chega ao resultado

Atualizar a tela consulta o estado da importação; não executa a leitura do arquivo.
O limite de espera do navegador não corrige falhas no processamento do worker.
Se a nota permanece em `queued`, conferir os logs, a fila/DLQ, a versão da imagem
e `PRO_FISCAL_ENABLED` no worker, além da agenda `fiscal_maintenance`.

O adaptador fiscal usa a leitura de flags em `app.application.runtime_environment`,
disponível na API e na imagem reduzida. `app.security_baseline` continua exclusivo
da API. O contrato de build importa também o adaptador fiscal e sua manutenção;
o canário executa `fiscal_xml` com uma NF-e sintética de 35 itens. Assim, uma
dependência ausente nesse caminho bloqueia a release antes da publicação.

Após publicar o worker corrigido e confirmar suas flags, a manutenção fiscal pode
redisparar importações pendentes ainda dentro da retenção. Verificar a agenda e o
resultado antes de orientar o cliente. Não renovar o prazo dos arquivos, alterar
estados manualmente nem solicitar um novo upload como recuperação padrão.

### Procedimento de implantação

1. Aplicar `backend/venv/Scripts/python.exe -m alembic upgrade head` com o banco
   de homologação configurado. Validar migração e smoke tests antes da produção.
2. Publicar API e worker compatíveis com o novo evento antes de liberar imports;
   publicar o PRO. Manter as quatro flags abaixo desligadas durante rollout.
3. Configurar invocação periódica (por exemplo, a cada cinco minutos) da Lambda
   com `{"action":"fiscal_maintenance"}`. Configurar DLQ, alertas de erros,
   importações presas, objetos expirados e capacidade/custos. Garantir permissões
   S3/KMS/SQS e CORS do bucket para a origem PRO. Manter lifecycle S3 como defesa
   adicional; o prazo do objeto não substitui o bloqueio na API.
4. Habilitar `PRO_FISCAL_ENABLED`, `PRO_TAX_ENABLED`, `PRO_REVENUE_ENABLED` conforme
   o piloto. `PRO_FISCAL_ENABLED` precisa coincidir em API e worker. A Central
   depende também de `PRO_FINANCIAL_CENTER_ENABLED` e de acesso PRO ativo.
5. `PRO_FISCAL_OCR_ENABLED` é opcional, desligada por padrão. Requer Textract
   configurado e orçamento aprovado; não usa a quota do conversor Free. PDFs
   digitalizados sem OCR são rejeitados com orientação de enviar XML.
6. Conferir dono/empresa, importação, revisão, sugestão, vínculo parcial,
   classificação, edição da origem, CSV e expiração. Verificar limpeza efetiva
   no banco/S3 e monitorar latência/volume no piloto.

Rollback operacional: desligar as flags, preservar **o worker compatível e a
agenda de limpeza**, e reverter a interface/API quando necessário. Mensagens
fiscais já enfileiradas exigem worker compatível; não voltar imediatamente ao
worker antigo. O downgrade para `20261115_01` mantém tabelas e dados quando há
qualquer registro fiscal; reaplicar upgrade é idempotente. Antes da adoção, o
downgrade remove somente as cinco tabelas novas comprovadamente vazias, sob lock
exclusivo e na mesma transação. Isso preserva o downgrade das versões anteriores
sem deixar FKs de tabelas vazias bloqueando a operação. Não há backfill nem
remoção de dados. Desabilitar escritores fiscais antes de executar downgrade. A limpeza
continua necessária mesmo com funcionalidades desligadas. A migration só cria
tabelas/índices novos, sem varrer movimentos para transformar dados; usa locks de
DDL breves, com tempo de aplicação a verificar na infraestrutura de produção.
Se o schema vazio foi removido, a limpeza retorna zero. Com registros retidos,
rollback destrutivo das tabelas antigas continua bloqueado pelas dependências.

## Acompanhamento da importação no PRO

A lista fiscal consulta importações pendentes a cada quatro segundos, sem
desmontar os documentos e controles já exibidos. Uma nova consulta periódica
só começa depois da conclusão da anterior; cada consulta tem limite de 30 segundos.
Respostas tardias são descartadas após cancelamento, troca de contexto ou saída da tela.
Atualizações da Central ao retornar à aba também preservam a tela fiscal.

Após dois minutos com o mesmo conjunto de importações pendentes, a consulta
automática é pausada e o PRO oferece o botão existente **Atualizar**. Essa ação
consulta o andamento e reinicia a janela de acompanhamento, sem reenviar o XML
nem iniciar outra avaliação na calculadora Java. A pausa do navegador não cancela
o processamento já enviado. Conclusão, falha e erro de consulta interrompem o
acompanhamento automático. Respostas com acesso já expirado são ocultadas e
não provocam ciclos de atualização imediata.

Se a importação continuar pendente, conferir seu estado no backend e o consumo
da fila de conversão. Um `GET /fiscal` com HTTP 200 confirma a leitura da lista,
não a conclusão da nota. O acompanhamento da importação é independente das
consultas de resultado de `classification-shadow`.

## Validação reproduzível

- `test_pro_fiscal_documents.py`: leituras, campos ausentes, XML malicioso,
  sentidos, valores exatos, sugestões e bases independentes de receita.
- `test_pro_fiscal_service.py`: PostgreSQL real, dono/expiração, idempotência,
  concorrência de alocações, revisão de origem/empresa, paginação, substituição,
  retenção e limpeza. Inclui substituta antes da original, cadeias fora de ordem
  e limite de tentativas após interrupção abrupta.
- `test_pro_fiscal_http.py`: servidor HTTP real; importação, download, vínculo,
  classificação, CSV, negativas de origem/acesso e downgrade/upgrade com dados.
- `test_pro_fiscal_runtime.py`: integridade dos bytes, roteamento SQS e limpeza
  independente das flags. `fiscal-workspace.test.tsx`: confirmação explícita,
  indicadores separados e remoção de dados antigos ao trocar empresa.
- `test_pro_fiscal_browser.py`: build PRO local com
  `PRO_FISCAL_BROWSER_URL=http://127.0.0.1:3216`; navegador, HTTP e PostgreSQL reais,
  confirmação de vínculo, classificação independente e viewport móvel de 390 px.
  Capturas sintéticas ficam em `tmp/pro-fiscal-browser/`.

Usar `CANONICAL_MOVEMENTS_TEST_DATABASE_URL` somente com PostgreSQL descartável.
No CI, os módulos de serviço e HTTP fiscal fazem parte da seleção explícita da
etapa PostgreSQL, que fornece essa variável; não dependem da etapa geral sem banco.
No Windows seguir `AGENTS.md`: pytest fora do sandbox, runner completo
`scripts/test-backend.ps1`, lint e suíte PRO. `/analyze` está aposentado (405);
compatibilidade HTTP é validada pelo fluxo vigente `/convert` e download.

Próximas etapas possíveis: corpus real autorizado de layouts municipais,
recebíveis parcelados com taxas/descontos explicados, retenção fiscal permanente
definida pelo produto, consulta oficial de eventos e apuração tributária. Esta
entrega não calcula obrigação fiscal nem substitui uma escrituração.
