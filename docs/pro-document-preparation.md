# Preparação de documentos no PRO

## Fluxo do escritório

1. Abra os documentos do cliente e escolha **Importar documentos**. O envio mantém o cliente selecionado.
2. Após o envio, a revisão abre o original junto dos dados extraídos. O sistema tenta identificar o cliente por CPF/CNPJ ou conta de origem, quando existe uma única correspondência segura. Nomes parecidos e identidades ambíguas não geram associação automática.
3. Confira cliente, conta e os avisos de leitura. Escolha **Confirmar e próximo** uma vez por arquivo. Essa confirmação também vale para notas e comprovantes; a lista de documentos não exige outra confirmação do mesmo conteúdo. Alterações posteriores nos dados voltam a exigir revisão.
4. Comprovantes e notas recebem sugestões de vínculo com os movimentos disponíveis na Central Financeira. Um identificador de operação único, valor, moeda e data compatíveis permitem vínculo automático. Apenas valor/data, duplicidade, possível transferência interna, estorno, pagamento parcial ou busca incompleta exigem conferência. É possível desfazer um vínculo; a próxima visita não o recria automaticamente.
5. Ao concluir, escolha **Preparar entrega**. Arquivos disponíveis, revisados e do mesmo cliente ficam selecionados, até o limite do pacote. Pendências e exclusões continuam explícitas antes do download.

Vínculo documental não confirma pagamento, não altera valores e não declara conciliação bancária. As regras de extração e os avisos financeiros são os mesmos no Free e no PRO.

Cliente e conta de extratos já integrados à Central Financeira continuam sujeitos à restrição existente de reassociação. O revisor apresenta esses campos bloqueados. Para documentos ainda não integrados, a escolha é salva com controle de versão durante a confirmação. Quando não há clientes cadastrados, a revisão oferece acesso ao cadastro em outra aba e atualização da lista.

## Retenção e acesso

- Importações PRO e documentos fiscais ficam guardados durante a vigência paga e por 30 dias após seu término efetivo. Cancelar uma renovação futura não deve antecipar `ended_at`: ele representa o fim do acesso pago.
- Assinatura ativa sem data de término usa uma data interna sem exclusão agendada. Essa data não é exibida como prazo para o cliente. O plano privado de escritório exige vigência finita.
- Renovação dentro dos 30 dias preserva os documentos ainda disponíveis. Documentos expirados ou já removidos não são recuperados.
- Durante os 30 dias, consulta e download permanecem disponíveis. Revisar, editar, associar e reprocessar exigem plano ativo, verificado no banco mesmo quando a sessão ainda possui identidade em cache.
- Conversões Free e períodos de teste não adquirem retenção paga. Seus prazos temporários existentes permanecem aplicáveis.
- A manutenção remove originais, resultados, versões de exportação e tentativas registradas. Falhas de armazenamento são repetíveis; o trabalho só recebe a marca de remoção após a exclusão. A indisponibilidade do banco não autoriza apagar um artefato por seu prazo JSON antigo.

## Publicação coordenada

Dependência: [infraestrutura de retenção, PR 12](https://github.com/claston/gettdone-infra/pull/12). **Não publicar apenas a interface nem aumentar globalmente `ANALYSIS_TTL_SECONDS`.**

1. Aplicar primeiro o change set da foundation: exclusão por idade apenas para objetos `ofx-retention=temporary`, com permissões de tags e exclusão. Objetos antigos sem tag ficam protegidos durante a classificação.
2. Reservar uma janela curta para suspender operações de documentos, incluindo leitura/download nos processos antigos, novos envios e processamento. Drenar as instâncias antigas: seus leitores ainda podem remover arquivos pelo prazo de 24 horas. Não manter leitores antigos atendendo durante esta transição.
3. Executar `alembic upgrade head` com o ambiente virtual do backend e o banco/schema corretos. Publicar API e worker compatíveis antes de reabrir as operações. A revisão `20261128_01` acrescenta colunas, tabela, índices e gatilhos; não remove nem renomeia estruturas.
4. Executar `scripts/backfill_document_retention.py` em modo de auditoria com `DATABASE_URL`, `DATABASE_SCHEMA`, `CONVERSION_S3_BUCKET`, `CONVERSION_S3_PREFIX` e `CONVERSION_RESULTS_S3_PREFIX`. Conferir os totais e repetir com `--apply`. O script preserva outras tags e nunca rebaixa uma tag `paid`. Manter os escritores antigos drenados até terminar a classificação.
5. Publicar o worker com as permissões atualizadas e habilitar `EnableFiscalMaintenance=true`. A rotina existente roda a cada cinco minutos e continua limpando documentos com importações fiscais desabilitadas. A nova imagem e a migração precisam estar presentes antes de habilitá-la.
6. Para sugestões/vínculos, manter `PRO_FINANCIAL_CENTER_ENABLED=true` e os movimentos do cliente disponíveis na Central. A preparação não habilita coortes nem promove silenciosamente extratos antigos a outra autoridade financeira.
7. Fazer o teste de aceitação abaixo e reabrir as operações. Nenhuma dessas ações em produção é executada pelo PR.

## Validação e observação

Em ambiente com PostgreSQL descartável, definir `CANONICAL_MOVEMENTS_TEST_DATABASE_URL` e executar `backend/venv/Scripts/python.exe scripts/test_backend.py`. O runner escolhe a concorrência pela memória, mantém JUnit/tempos em `tmp/test-runs` e remove seu diretório temporário. Executar também os testes, lint e build do PRO. O cenário `scripts/smoke_document_preparation.py` integra a suíte de navegador `workspace`; usa documentos sintéticos, desktop e celular, confirmação única, avanço, seleção para entrega e consulta sem edição durante a carência.

Os testes de integração cobrem retenção paga e Free, fim de vigência, renovação, ausência de ressurreição, limpeza repetível, metadados da Central, migração/downgrade/reupgrade, associação por titular, vínculo automático e desfazer, edição financeira invalidando vínculo, isolamento de proprietário e HTTP real.

Antes de liberar produção, com uma conta e arquivos sintéticos:

1. Importar extrato e comprovante, conferir o original e confirmar cada arquivo uma vez. Verificar entrega e download dos arquivos selecionados.
2. Conferir um vínculo seguro e uma sugestão ambígua; desfazer o seguro e reabrir para verificar que não reaparece. O saldo e os valores do extrato devem permanecer iguais.
3. Encerrar a vigência mantendo a sessão aberta. Confirmar GET/download permitido, POST de preparação e alterações negados, e data de exclusão em 30 dias.
4. Renovar antes do prazo e conferir retomada da edição. Em outro documento de teste, ultrapassar a carência, executar manutenção e conferir remoção física dos prefixos no S3 e ausência de acesso. Renovar depois não deve restaurá-lo.

Acompanhar erros do worker, `expired_analyses_removed`, `expired_pro_documents_removed`, idade dos trabalhos vencidos ainda sem `pro_document_purged_at`, tamanho do bucket, objetos sem tag e falhas de banco/locks. A retenção mais longa aumenta o volume de armazenamento. Os índices adicionados e a atualização dos documentos disponíveis podem segurar locks na migração; dimensionar a janela com uma cópia representativa do banco. A validação local não mede o volume real de produção.

## Rollback

O downgrade da nova revisão é intencionalmente conservador: mantém estruturas e proteção de retenção. A reaplicação é idempotente. Os testes exercitam downgrade/reupgrade preservando trabalhos e vínculos existentes; não há exclusão estrutural nem backfill reversível de documentos já apagados.

Para reverter a interface, manter armazenamento, worker e schema compatíveis com retenção paga. Não voltar a uma regra S3 que exclui todo o prefixo por idade. Se for necessário usar binários anteriores à retenção, manter as operações de documentos suspensas até disponibilizar a correção de compatibilidade; um simples rollback completo da imagem antiga não é seguro para o novo prazo de guarda.
