# Fluxo de documentos do PRO — OFX-79 / OFX-80

Implementação no PRO existente, reaproveitando a OFX-59 (PR #628), o upload assíncrono, Clientes, o revisor e os exportadores existentes. Recebimento, organização, conferência e pacote usam a mesma autorização do plano PRO, sem flags de acesso para Escritório ou organização. Não substitui o conversor Free nem implementa a Central Financeira, conciliação contábil, portal de clientes ou captura fiscal.

## Cobrança decidida pelo produto

- Novo código privado `escritorio_documentos`, modalidade `document_conversion`.
- Um PDF convertido com sucesso em dados estruturados consome uma conversão, dentro dos limites do plano. A cobrança existente usa o ID do trabalho para impedir consumo duplicado em uma repetição.
- OFX, CSV e Excel `.xlsx` não consomem conversões nesse plano, inclusive com saldo zero. A classificação por extensão não habilita outro parser; conteúdo inválido continua sujeito à validação existente.
- Conferir, associar a cliente, corrigir tipo, baixar páginas e gerar novamente um pacote não consomem conversões. Arquivo que falha antes de produzir resultado não é cobrado.
- Preço e cota não são semeados. Sem ambos, a versão privada permanece inativa. Planos públicos e contratos atuais não são atualizados.
- Os limites de OCR são limites de uso/custo já existentes, independentes da unidade de cobrança por documento. Não foi habilitado OCR universal.

## Matriz de entrega

| Fluxo | Código reaproveitado / complemento | Validação |
| --- | --- | --- |
| Receber lotes mistos | `conversion_batch_service`, `conversion_lambda`, OFX-59; `job.product` separa o processamento PRO do Free | HTTP com PDF bancário, comprovante e nota; classificações sem movimentos de apoio; formatos estruturados com cota zero |
| Separar anexos | Segmentação existente e `document_parts`; PDF derivado sob demanda em `office_documents.part_bytes` | Página de comprovante separada preserva referência; agrupamento incerto exige original; não representa contagem de notas |
| Organizar cliente/mês/conta | `pro_documents`, Clientes e organização existentes; datas dos movimentos ou data documental inequívoca | Mês operacional, documentos sem data em “Período a confirmar”, vínculo manual com autor/versão/expiração |
| Conferir | Revisor bancário existente; `OfficeReviews` para conferência manual independente | Comparação de versão, repetição idempotente, conflito entre conexões PostgreSQL, auditoria e expurgo |
| Entregar | Exportadores canônico e existente; `/api/pro/documents/package/*` | Prévia, confirmação das exclusões/ressalvas, versões atuais, expiração, isolamento de usuário, tamanho, nomes seguros |
| Plano e acesso | Cadastro privado versionado e ativação/cancelamento administrativo | HTTP autenticado, repetição sem novo contrato, fim do acesso, catálogo público inalterado |
| Free | Política padrão restaurada ao `main` `11f69a3`; novos comportamentos só no comando PRO persistido | Comparação sintética de conversão e revisão sem aprovações de diferenças; testes antigos preservados |

O pacote seleciona documentos inteiros. O mês não recorta movimentos, não comprova cobertura nem reconciliação. A prévia e os manifestos explicam isso. A navegação mensal tem consulta limitada a 20 páginas; se houver continuação, mostra contagem **parcial** e acesso à lista do cliente. As contagens não representam valores financeiros.

## API do piloto

Todas as rotas de documentos exigem proprietário autenticado com PRO ativo. Escritas também usam a proteção de origem/cliente PRO existente.

- `GET /api/pro/documents?client_id=...&month=AAAA-MM&account_id=...` ou `month=unknown`.
- `POST /api/pro/documents/{job}/verification`: `source`, `expected_version`, `operation_id`, `status` (`pending`, `verified`, `attention`, `unprocessable`), tipo opcional para organização. Alterar tipo de um extrato com dados exige o revisor existente.
- `GET /api/pro/documents/{job}/parts/{part}`: PDF derivado autorizado, sem nova extração ou retenção.
- `POST /api/pro/documents/package/preview`: `client_id`, `job_ids` (até 30), `month` opcional. Retorna itens, exclusões, ressalvas e `snapshot`.
- `POST /api/pro/documents/package/download`: mesma seleção, `snapshot` e `accept_partial` para reconhecer exclusões/ressalvas. Uma edição/expiração/alteração de associação invalida a prévia. ZIP máximo de 64 MB de conteúdo, gerado em memória, `private, no-store`.

Manifestos JSON e CSV contêm origem/lote, tipo, conta mascarada, datas, versão, conferência, arquivos e ressalvas. Duplicatas binárias na seleção não geram outro conjunto de movimentos. Itens expirados têm apenas identificador e motivo; nomes e dados financeiros expirados não são copiados para o manifesto. O CSV neutraliza fórmulas em texto fornecido por usuários.

## Cadastro e operação comercial

As rotas abaixo usam **sessão administrativa existente**, nunca token em URL. Não executar contra produção antes da decisão de lançamento.

1. `POST /admin/plans/office/configure` com `operation_id` único e `version`. `price_cents` e `quota_limit` podem ser omitidos para cadastrar um rascunho inativo. Os limites configuráveis são `max_pages_per_file` (até 100), `max_pages_per_file_ocr` (até o limite de páginas) e `batch_max_files` (até 30). O runtime e a política de OCR podem impor limites menores.
2. Após preço/cota aprovados, cadastrar **nova versão** com esses valores. Não editar versões de contratos vigentes. O cadastro permanece privado; a página `/escritorio` descreve o piloto assistido sem publicar preço inventado.
3. Confirmar pagamento pelo provedor aprovado no processo comercial existente, sem guardar cartão na aplicação. Usar `POST /admin/plans/office/activate` com `operation_id`, `user_id`, `expires_at` futuro. O cadastro genérico de planos recusa ativar este plano sem término.
4. Renovação assistida usa nova operação de ativação após confirmação comercial. A repetição da mesma operação não reinicia a cota. Cancelamento usa `POST /admin/plans/office/cancel` com `operation_id` e `user_id`. Expiração/cancelamento removem os entitlements; não prolongam a retenção de documentos.

Configuração, ativação e cancelamento ficam em `office_commercial_events`, sem conteúdo financeiro dos documentos. Nenhuma assinatura real, preço, pagamento ou configuração de produção foi alterado por esta implementação.

## Migração e implantação

`20261122_01` é aditiva: coluna `conversion_jobs.product` com padrão `free`, tabelas de conferência/eventos/vínculos e eventos comerciais. Não faz backfill financeiro, não remove tabelas/colunas e não reescreve movimentos. A coluna usa valor padrão constante; o ALTER requer um bloqueio breve. Índices novos são sobre tabelas novas. Prever janela sem trabalhos de migração concorrentes e monitorar tempo de bloqueio.

1. Aplicar a migração antes de publicar a versão PRO que usa essas tabelas.
2. Implantar a mesma revisão na API e em todos os workers antes de admitir novos envios PRO. Processos anteriores ao marcador de produto não podem processar esses trabalhos.
3. Validar o caminho assíncrono existente, autorização, armazenamento e exportador na coorte. Manter a fonte canônica quando adotada; nunca restaurar escrita legada de uma análise canônica.
4. Agendar/manter `app.workers.canonical_cleanup`: também remove conferências, eventos documentais e vínculos manuais no prazo original. O scheduler deve executar mesmo se a admissão de novos documentos estiver desligada.
5. Validar acesso com o plano PRO existente e smoke de preview com arquivos sintéticos. Não há variável adicional de liberação. Não mudar o TTL existente (86400 s quando usado o padrão). A data real de expiração aparece em cada documento.
6. Aprovar preço, cota, retenção divulgada e pagamento, ativar os pilotos e acompanhar a primeira entrega. Teste local não substitui a validação de usabilidade por escritório real.

Rollback: interromper a admissão de novos envios PRO no controle operacional de tráfego, concluir/drenar os trabalhos PRO em curso e só então retornar os binários. A migração de downgrade preserva os objetos aditivos e o marcador de produto, para não converter trabalho pago pelo caminho Free. Reaplicar upgrade é idempotente. Conferências e documentos continuam sujeitos ao expurgo e à autorização; não apagar evidências para reabilitar um escritor antigo.

`PRO_OFFICE_ENABLED` e `PRO_ORGANIZATION_ENABLED` foram removidas e valores antigos são ignorados. `/api/pro/documents/options` mantém resposta habilitada para frontends já carregados; a interface atual não consulta esse endpoint. O worker registra recebimentos de trabalhos PRO automaticamente, preservando o Free. Preço/cota continuam configuráveis no cadastro privado e não funcionam como liberação separada das telas.

As tabelas de metadados preservados não possuem chaves estrangeiras que impeçam rollback profundo da organização ou apaguem a auditoria em cascata. Todas as operações resolvem a organização do proprietário autenticado antes de consultar ou gravar. Registros sem organização ativa permanecem inacessíveis e são expurgados no prazo original. O teste de rollback existente até `20261012_01` também é executado.

## Observabilidade e gates

Eventos novos em `product_events` usam somente tipo e identificador de conta: upload, cliente associado, conferência, pacote e ativação/renovação/cancelamento. Os eventos existentes de revisão/download e os registros de custo/duração/OCR/falha complementam o funil. Nenhum novo evento recebe nome de arquivo, CPF/CNPJ, valor, texto extraído ou transação. Eventos não são fonte de cobrança; a cobrança usa o registro idempotente existente.

Antes de lançar: conferir primeiro lote misto → organização → revisão → ZIP; repetir com dois usuários e dois lotes; expirar um lote; corrigir movimento depois da prévia; verificar importações estruturadas com cota zero; acompanhar falha de processamento e exclusões no manifesto. Validar implantação API/worker e tarefa de expurgo na mesma revisão. Preço, margem real, primeiro pagamento e aceitação por escritórios permanecem decisões/validações comerciais, não resultados inferidos dos testes automatizados.

## Evidência local — 22/11/2026

- Free contra `main` `11f69a3`: `check_pdf_compatibility.py` aprovou 90 casos e `check_review_compatibility.py` aprovou 170, sem diferenças ou aprovações de exceções. O limite de desempenho não foi medido por esses comandos.
- Backend completo com PostgreSQL descartável: 5.770 aprovações, 4 skips e 2 xfails; encontrou uma falha no rollback profundo, corrigida removendo a dependência referencial dos metadados retidos. O aceite final executou novamente esse rollback, os testes Escritório e os dois navegadores: **20 passed** em 74,76 s (`tmp/ofx79-acceptance.xml`).
- Frontend completo: **477 passed** em 77 arquivos; repetição das quatro telas afetadas: **23 passed**. Ruff, ESLint, TypeScript, lint de navegação e texto UTF-8 aprovados.
- Build de produção aprovado com `NEXT_PUBLIC_API_URL=/`. Navegador real → HTTP local → PostgreSQL: pacote com OFX e manifesto, desktop 1440 px e celular 390 px, sem transbordamento; revisão concorrente com dois editores também aprovada. Imagens sintéticas em `tmp/ofx79-browser/`.
- HTTP real: lote com extrato/nota/comprovante, conferência e correção de tipo, downloads, pacote, edição invalidando prévia, usuário alheio, expiração sem metadados no manifesto, limites de tamanho/quantidade; OFX/CSV/XLSX com cota esgotada sem novos débitos; cadastro e ativação administrativa autenticados. O endpoint `/analyze` está aposentado (405); o caminho vigente é `/convert`/lote, com `GET /report/{id}` e negativos exercitados.

Essas evidências usam arquivos sintéticos e serviços locais; não atestam deploy AWS, configuração de produção nem aceitação por um escritório cliente.

## Simplificação do acesso PRO — 22/11/2026

Após o merge de #629, a orientação de produto unificou o fluxo no PRO existente. Foram removidas as duas flags de acesso e a consulta de opções da interface. O plano configurável por conversões permanece privado, sem preço/cota inventados; planos existentes não são reescritos. O worker registra apenas novos trabalhos marcados como PRO; a importação pelo Free permanece no caminho existente.

Validação da correção contra `main` `e41a4c4`: backend completo **5629 passed, 150 skipped, 2 xfailed** (604,44 s, seleção completa do runner); frontend **478 passed**; HTTP real e worker **45 passed**; conversão Free **90 casos sem diferenças** e revisão Free **170 sem diferenças**, sem aprovações de exceções. Integrações PostgreSQL/navegador dependentes de ambiente estão entre os skips locais e possuem job próprio no CI. Build, Ruff, ESLint, TypeScript, texto UTF-8 e navegação passaram. Os três cenários de navegador da suíte workspace passaram, incluindo Clientes/Documentos, importação e exportação em desktop e celular, sem consultar uma liberação adicional.

<a id="atualizacao-da-base-apos-o-merge-da-ofx-59"></a>

## Atualização da base após o merge da OFX-59

O squash de #628 entrou na `main` como `cd6b2fb`, com árvore idêntica ao commit `f665a71` já incorporado pelo #629. A integração preserva integralmente o código do produto validado em `09ed1f8` (árvore `backend/app`: `2a502e0edf081c83aaa64058f6dfe62aa2ade496`). Não introduz outro parser, revisão ou fluxo.

O usuário já havia determinado que o Free continuasse como no início desta tarefa, na base `11f69a3`. A nova base contém o identificador global de classificação da OFX-59. Comparada com ela, a implementação isolada do #629 restaura somente o metadado de versão de `2026-11-22.1` para `2026-10-27.1`: três diferenças na conversão e 80 na revisão, todas em campos `document_classification_version` ou `financial_document.classification.version`. Os 90 casos de conversão e 170 de revisão não apresentam nenhuma outra diferença de caracterização; o baseline congelado também passa.

As diferenças exatas estão em `transaction-document-approvals.json`, vinculadas exclusivamente à referência `cd6b2fb` e à árvore de aplicação acima, como evidência da preservação já autorizada do Free. Não há wildcard, relaxamento do teste, alteração de baseline congelado ou autorização para mudar movimentos, valores, sinais, FITIDs e procedência. Contra a base original `11f69a3`, seguem as comparações já aprovadas sem diferenças. Os 80 testes dos arquivos envolvidos na integração foram repetidos e aprovados, incluindo HTTP real do Escritório.
