# Conferência documental no PRO

A tela do cliente organiza o trabalho em três áreas: **Conferência**, **Arquivos** e **Entrega**. A Conferência abre primeiro quando a Central Financeira está disponível. Links existentes para uma categoria ou um lote continuam abrindo os arquivos correspondentes.

## Fluxo

1. Selecione cliente, conta e mês. Sem mês informado, a conferência consulta o mês mais recente com movimentos; o mês efetivamente consultado aparece acima da lista.
2. Escolha uma movimentação e compare seu extrato com uma nota ou comprovante do mesmo cliente. Documentos de outros períodos também podem ser escolhidos, pois emissão e pagamento podem ocorrer em meses diferentes.
3. Consulte os originais, reconheça explicitamente a relação e confirme. Vários documentos podem apoiar a mesma movimentação. É possível desfazer cada relação.
4. Em Arquivos, consulte um documento por vez e use **Comparar com movimentação** para iniciar pelo comprovante ou pela nota. A escolha do movimento continua sendo manual.
5. Em Entrega, selecione os arquivos e confira o pacote pelos critérios existentes. Os vínculos novos não substituem a revisão bancária ou a verificação manual exigidas pelo pacote, nem são exportados como uma conciliação.

Em telas pequenas, a seleção abre o detalhe e o botão de voltar restaura a lista. Correção de tipo e estados secundários ficam em opções expansíveis.

Capturas da aplicação compilada com dados sintéticos: [desktop](assets/pro-document-conference/desktop.png) e [celular](assets/pro-document-conference/mobile.png).

## Significado e limites

O vínculo é uma referência documental, não uma alocação financeira: não altera valores, sinais, saldos, movimentos, classificação fiscal, quitação ou conciliação. Não há correspondência automática por valor/data. A interface alerta quando há outro movimento com o mesmo valor, moeda e data na página e preserva as indicações de possível duplicidade.

A confirmação registra a versão do movimento e uma impressão da versão atual do documento e de seu contexto. Uma revisão posterior exige nova conferência. A leitura usa a mesma autoridade do revisor, incluindo revisões cujo resultado ainda não foi projetado no lote. Cada leitura e comando verifica titular, cliente, assinatura ativa, disponibilidade da origem e validade temporal. O vencimento é limitado pelas origens; a conferência não prolonga retenção.

A API retorna no máximo 100 relações por movimento e informa explicitamente quando a lista está parcial ou quando há documentos indisponíveis. A lista de movimentos continua sujeita à cobertura da Central Financeira. A ausência de documentos ou movimentos não comprova ausência de atividade.

## Implantação e retorno

- Aplicar `alembic upgrade head` com a configuração de produção antes de implantar a API. A revisão `20261124_01` cria apenas `pro_document_movement_links`, `pro_document_link_events` e seus índices; não reescreve dados existentes.
- Implantar API e PRO. São usados os controles existentes `PRO_FINANCIAL_CENTER_ENABLED` e as permissões persistidas. A API anuncia `features.document_conference`; o frontend não tenta salvar vínculos quando conectado a uma API antiga sem essa capacidade.
- O SQL cria índices somente nas tabelas novas e não exige backfill. Não há indisponibilidade planejada. A criação inicial adquire os bloqueios normais de DDL; executar antes do rollout e observar duração/erros da migração.
- Para retornar, reimplantar as versões anteriores de API/PRO. O downgrade da revisão mantém as tabelas independentes e seus registros para permitir nova implantação sem perda. Nenhuma tabela antiga é removida ou renomeada e não há chaves estrangeiras que impeçam um rollback mais profundo.
- A limpeza existente de metadados elimina relações e recibos de operação vencidos. Enquanto uma versão antiga estiver ativa, essas tabelas adicionais podem aguardar a retomada da limpeza nova; não há leitura de arquivos vencidos pela API antiga.
- Após implantação, conferir o recurso anunciado, abrir um cliente autorizado, relacionar/desfazer um documento e verificar respostas 403/404/409 e erros 5xx. Alertas existentes de HTTP/limpeza continuam aplicáveis. Não registrar conteúdo financeiro em diagnósticos.

## Validação

`backend/tests/test_document_conference.py` exercita PostgreSQL real, concorrência, repetição idempotente, versões, outro titular, validade, autoridade do revisor, migração/downgrade/reaplicação, limpeza e HTTP real. Os testes React cobrem seleção, confirmação explícita, navegação por documento, preservação do pacote e avisos de conferência incompleta.

`backend/tests/test_document_conference_browser.py` usa extrato, comprovante e nota **sintéticos**, navegador Chromium e API/banco reais. Para executar, iniciar um build local do PRO com `NEXT_PUBLIC_API_URL=/` e `PRO_FINANCIAL_CENTER_ENABLED=true`; definir `DOCUMENT_CONFERENCE_BROWSER_URL` para essa URL local e `CANONICAL_MOVEMENTS_TEST_DATABASE_URL` para um PostgreSQL descartável permitido pela fixture. Executar pelo Python do venv, fora do sandbox no Windows, com `--basetemp` específico em `%LOCALAPPDATA%/Temp`. O teste salva imagens em `local/ux-conferencia-pro/implemented/`.

Também executar o teste existente `test_office_browser.py` com `OFX70_BROWSER_URL`, para verificar seleção, conferência e download do pacote. A API de upload suportada é `/convert`; `/analyze` permanece desabilitada (405), conforme os testes HTTP existentes.
