# PDFs compostos e interpretação do OCR básico

## Comportamento

Um PDF pode conter páginas de extrato, notas fiscais e comprovantes. A conversão classifica as páginas usando o texto já extraído, preserva sua numeração original e informa os grupos encontrados no campo opcional `analysis.document_parts`.

Quando há um extrato e anexos reconhecidos, somente as páginas do extrato entram na extração de lançamentos. NF-e, NFS-e e comprovantes aparecem na prévia como conteúdo separado dos movimentos. Páginas desconhecidas ou com tipos misturados recebem aviso de revisão. Uma continuação sem cabeçalho exige sinais de movimentos bancários antes de herdar o tipo da página anterior.

Uma nota fiscal no início de um PDF com texto nativo não impede a leitura de um extrato posterior. Uma nota fiscal isolada continua sendo rejeitada como documento incompatível. Contas ou períodos distintos identificados no mesmo arquivo são rejeitados para evitar a fusão de extratos.

A separação desta entrega é lógica: grupos de páginas e exclusão dos anexos dos movimentos. O download de cada anexo como um PDF independente pertence à etapa posterior de arquivos derivados.

## Palavras e origem dos dados

O modelo `ExtractedWord` preserva texto, página, confiança, tipo de texto, caixa e polígono de posição. Linhas e células guardam os IDs das palavras correspondentes. A extração mantém o identificador da operação, pois os IDs de blocos do provedor têm escopo nessa operação.

Esses dados atravessam os adaptadores de parsing e podem ser armazenados no `source-evidence.json` restrito do diagnóstico já existente. Não são adicionados ao payload público da análise. A autorização, expiração e classificação de privacidade do diagnóstico permanecem as mesmas; seu volume aumenta por preservar a evidência das palavras. Modelos antigos sem esses campos continuam válidos.

Os campos são retornados pelo OCR básico; sua preservação não faz outra chamada. Consulte o [contrato Block do Textract](https://docs.aws.amazon.com/textract/latest/APIReference/API_Block.html).

## Extrato mensal Itaú com aplicações automáticas

O parser de OCR desse layout usa o cabeçalho e a posição das palavras para associar valores às colunas de entradas, saídas e saldo. Ele distingue números que o OCR juntou em uma linha e preserva a data de grupos que continuam na página seguinte. Débitos recebem o sinal da coluna de saídas; traços manuscritos ao lado de créditos não os transformam em débitos.

`TextType` funciona como evidência auxiliar. Anotações marcadas como impressas não são automaticamente consideradas dados financeiros. Um valor financeiro marcado como manuscrito, um valor sem associação clara à descrição ou uma linha sem data suficiente gera pendência de revisão. Não se eliminam movimentos por terem o mesmo valor: duas transferências legítimas iguais permanecem distintas.

O parser termina a seção da conta corrente antes de saldos consolidados, totalizadores e tabelas de aplicações. O saldo anterior impresso nesse modelo pode combinar conta corrente e aplicações. Nesse caminho, `opening_balance` fica vazio e a prévia pede sua conferência, em vez de usar o valor consolidado como saldo inicial da conta corrente.

As métricas opcionais são:

| Campo | Significado |
| --- | --- |
| `ocr_unresolved_rows` | Pendências de associação de linhas ou valores que exigem revisão. |
| `ocr_annotations_detected` | Indícios de anotações encontrados nas páginas examinadas; não garante detectar todas. |
| `opening_balance_ambiguous` | Saldo inicial não utilizado por ambiguidade de escopo. |

Pendências financeiras mantêm `export_recommendation=review_recommended`. A prévia identifica linhas que ficaram fora da conversão e pede comparação com o original. A política atual de exportação com revisão permanece em vigor.

## Custo e implantação

O gateway permanece no modo configurado anteriormente. Esta entrega não ativa TABLES/LAYOUT, não acrescenta um OCR de classificação e não inicia escalada automática. O reconhecimento de anexos reutiliza a extração já realizada.

O parser por posição se aplica ao layout mensal Itaú reconhecido. Os demais layouts mantêm seus parsers. PDFs nativos usam o texto já disponível; escaneados continuam seguindo a política de OCR existente. A seleção de páginas para reduzir OCR em PDFs híbridos, o orçamento de escalada avançada e o cache durável de jobs são etapas posteriores do plano.

O contrato HTTP recebe somente campos opcionais. A estrutura persistente continua sendo o JSON temporário da análise e do diagnóstico; não há migração de banco de dados nem mudança de permissões do worker neste PR.

Rollback: reverter o commit de implementação e o frontend correspondente. Os campos novos são aditivos e resultados antigos continuam legíveis. O modo do Textract e os limites de upload/OCR não precisam ser alterados para essa reversão.

## Validação

As fixtures sintéticas cobrem crédito próximo de traço manuscrito, anotações marcadas como impressas, ausência de metadados, células relacionadas a palavras, anexos antes e depois do extrato, NFS-e sem chave de NF-e, continuação de datas, valores iguais legítimos, cabeçalhos repetidos e valores sem associação segura.

Nos dois exemplos privados investigados, a validação recebeu novas respostas de `DetectDocumentText` para as cinco páginas com movimentos de conta corrente. As demais páginas reutilizaram a evidência básica já capturada. As respostas foram salvas localmente para que a repetição dos testes não execute novas chamadas pagas. Nenhuma análise TABLES/LAYOUT foi necessária nessa validação.

A aplicação local foi executada com armazenamento e identidade isolados. Requisições HTTP reais validaram o upload de PDF composto nativo, os dois PDFs escaneados com respostas básicas salvas, download de relatório, CSV suportado, nota fiscal isolada e análise inexistente. Os exemplos resultaram em 33 e 42 lançamentos, com anexos excluídos, nenhuma linha financeira pendente e nenhuma inconsistência detectada nos saldos intermediários. O saldo inicial permaneceu explicitamente pendente de conferência.

`POST /convert` é o upload vigente. `POST /analyze` permanece com HTTP 405; o checklist histórico do repositório deve ser interpretado considerando essa compatibilidade.

Os PDFs, respostas de OCR e artefatos privados não são versionados. Os resultados específicos não substituem a revisão do usuário nem garantem a interpretação de layouts diferentes.
