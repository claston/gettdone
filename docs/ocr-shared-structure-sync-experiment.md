# OCR compartilhado: experimento síncrono e proposta para o PRO

Data: 31/10/2026. Estado: prova isolada; integração ao conversor e disponibilização no PRO aguardam aprovação.

## Resultado e decisão proposta

A referência é um extrato Asaas digitalizado de quatro páginas e 72 movimentos conferidos visualmente. A primeira página tem inclinação e descrições multilinha e havia sido omitida pelo leitor de texto existente.

| Leitura Textract | Interpretação | Movimentos | Datas, valores, sinais, multiplicidade e páginas | Saldos impressos |
| --- | --- | ---: | --- | --- |
| DetectDocumentText | Caminho atual | 53/72 | Faltam 19 movimentos da primeira página | Conferência não registrada |
| DetectDocumentText | Estrutura compartilhada experimental | 72/72 | Iguais à referência, sem extras | Conferem |
| AnalyzeDocument, TABLES + LAYOUT | Caminho atual | 72/72 | Iguais à referência, sem extras | Conferência não registrada |
| AnalyzeDocument, TABLES + LAYOUT | Estrutura compartilhada experimental | 72/72 | Iguais à referência, sem extras | Conferem |

A distribuição é 19, 25, 25 e 3 movimentos por página. As descrições produzidas pela camada compartilhada são iguais entre palavras e células, após normalizar somente espaços. Essa equivalência não substitui certificação visual completa de cada contraparte e descrição.

Na primeira página, a lista de linhas do OCR traz `Valor` antes de `Data` e `Movimentações`. O leitor Asaas existente só inicia a tabela quando encontra `Movimentações` seguido de `Valor`. Na reprodução, essa dependência da ordem textual impede a entrada na primeira página. A reconstrução comum utiliza as posições e elimina essa dependência sem alterar o parser existente.

O ganho comprovado é recuperar 19 movimentos com reconstrução estrutural e usar uma única interpretação financeira. Nesta amostra, **AnalyzeDocument não recuperou movimentos adicionais em relação ao texto reconstruído**. Não há evidência para torná-lo o padrão de todos os PDFs pagos.

Ambos os resultados experimentais continuam em revisão por confiança baixa de OCR em campos e controles. Os saldos fecharam sem escolher números para forçar o resultado, alterar sinais ou remover repetições. O limiar experimental de confiança é 90%; scores de WORD e CELL têm escopos diferentes e não foram calibrados como medidas equivalentes. Contagem de avisos não prova ganho de precisão.

Recomendação: evoluir a estrutura compartilhada de OCR e, após aprovação e validação adicional, disponibilizar análise de tabelas como opção explícita, limitada e paga. Preservar o processamento nativo e o caminho padrão durante o piloto.

## Execução autorizada

- Um PDF, quatro páginas, oito chamadas pagas: quatro `DetectDocumentText` e quatro `AnalyzeDocument`, síncronos, em `us-east-1`. Nenhuma API `Start*`.
- Cada página foi separada em um PDF de uma página. A comparação dos pixels renderizados confirmou identidade com a página original. Não houve retificação, binarização ou aumento de resolução.
- Os mesmos pixels foram usados nos dois modos. Resultados foram recompostos na numeração física original; IDs e Relationships receberam prefixo de página para evitar colisões entre respostas independentes.
- As quatro cópias temporárias exclusivas no S3, incluindo suas versões, foram removidas ao concluir.
- PDFs, referência manual, respostas, hashes, descrições, comparações e manifestos ficam somente em `local/pdf-review-six-20261031/sync-january/`, ignorado pelo Git.
- As respostas foram obtidas uma vez; ajustes e comparações seguintes foram offline. Nenhum resultado histórico foi reescrito.

Tempo agregado observado das quatro chamadas: texto 16,090 s; análise 25,308 s. Inclui overhead do AWS CLI e não constitui benchmark de produção. Uploads e limpeza são adicionais. Os modos retornaram as mesmas palavras e linhas; análise acrescentou quatro tabelas e 228 células.

## Arquitetura implementada no protótipo

```mermaid
flowchart TD
    T[OCR com palavras posicionadas] --> E[Evidência comum existente]
    A[OCR com tabelas] --> E
    E --> S[Reconstrução de estrutura]
    S --> R[Linhas com campos e origem]
    R --> F[Uma interpretação financeira]
    F --> V[Cobertura, saldos e confiança]
```

1. O contrato existente `RawDocumentExtraction` preserva WORD, LINE, TABLE e CELL por meio do adaptador do fornecedor.
2. `ocr_structure.py` reconstrói linhas com células ou palavras, usando cabeçalhos e inclinação observada. Preserva descrições multilinha e referências à origem. Confere células contra âncoras independentes das palavras. Cabeçalhos ausentes podem ser recuperados por continuidade ou, na forma de três colunas, pelas âncoras explícitas de data e valor.
3. `ocr_financial_interpretation.py` interpreta as mesmas linhas nos dois modos. Usa Decimal, datas completas, sinais explícitos e controles impressos configurados. Mantém repetições legítimas e linhas não resolvidas.
4. `scripts/fixtures/ocr_signed_cash_schema.json` declara vocabulário, papéis de colunas e âncoras. Não contém banco, coordenadas de um documento ou regras diferentes por modo de OCR.

O núcleo estrutural não importa SDKs nem decide sinais financeiros. A interpretação financeira não escolhe entre fornecedores ou modos. Não foram criados dois parsers Asaas.

Os módulos novos estão fora do despacho de produção. Nenhum leitor nativo, parser existente, endpoint, plano, worker ou componente PRO foi alterado. O avaliador offline é o único consumidor fora dos testes.

### Limites

- A política experimental cobre um bloco de caixa, com data completa e uma coluna de valores com sinal. O núcleo é reutilizável; isso não comprova suporte universal a relatórios financeiros.
- Débito/crédito separados, faturas, posições e eventos sem efeito de caixa precisam de semântica própria, compartilhada pelos leitores. Não devem ser forçados ao modelo de caixa.
- Vários controles de abertura/encerramento exigem segmentação anterior por bloco e conta. O protótipo preserva movimentos e exige revisão, sem certificar um saldo global.
- Geometria ausente, data/valor ilegíveis, sinais conflitantes, página posterior sem evidência e cobertura incerta impedem liberação automática. Não se inventam mês, ano, valor, sinal ou associação de conta.
- Perspectiva, curvatura e rotações extremas precisam de corpus e tratamento próprios. Não foi medido ganho de pré-processamento de imagem.
- A contagem de âncoras comprova a evidência reconhecida, não que o OCR enxergou toda transação impressa. A referência visual independente continua necessária na avaliação.

## PRO: proposta concreta, ainda não implementada

Na tela `Nova conversão`, manter `Padrão` selecionado. Acrescentar `OCR avançado — análise de tabelas` para um único PDF que necessite de leitura visual.

Texto de ajuda:

> Para PDFs digitalizados com tabelas difíceis de ler. Pode levar mais tempo e ainda exigir revisão. Disponível em planos pagos, dentro do limite de OCR avançado.

Após preflight do servidor, informar páginas reais, consumo e saldo. Exemplo ilustrativo do limite proposto:

> 4 páginas. Consome 4 páginas da franquia do plano e 4 do limite de OCR avançado. Restarão 16 páginas de OCR avançado neste ciclo.

Botão: `Converter com OCR avançado`. O piloto proposto usa limite separado dentro do plano pago, sem cobrança avulsa. Preço avulso ou mudança comercial exige decisão explícita.

| Regra | Proposta inicial sujeita à aprovação |
| --- | --- |
| Acesso | Assinatura paga ativa e permissão resolvidas no servidor |
| Gratuito | Indisponível; requisição forjada recusada antes de chamar AWS |
| Arquivos | Um PDF por solicitação; sem lote de vários documentos |
| Páginas por arquivo | Menor valor entre o limite OCR do plano e 6 páginas |
| Franquia avançada | 20 páginas por ciclo mensal |
| Consumo | Páginas reais na franquia normal e no limite avançado; reserva idempotente antes de gastar |
| Nativo confiável | Mantém o caminho nativo; recusar opção avançada antes de gastar quando não precisa de OCR |
| PDF misto/camada OCR defeituosa | Classificar necessidade visual; presença de qualquer texto não prova qualidade nativa |
| Resultado | Pode permanecer em revisão; sem promessa de conciliação ou extração completa |
| Nova tentativa | Escolha explícita; sem escalada automática de texto para análise de tabelas |

As 20 páginas limitam Tables a aproximadamente US$ 0,30 por assinante/ciclo, antes de impostos, câmbio, armazenamento e repetição técnica. É orçamento proposto, não preço aprovado ou garantia de margem. O piloto deve declarar a família financeira validada e ampliar o suporte com novas evidências.

### Contratos e execução

- Adicionar modo opcional por solicitação, por exemplo `ocr_mode: standard | advanced_tables`, com default `standard`. Backend valida plano, arquivo e páginas reais; não confiar no estado do navegador.
- Adaptar `NewConversionForm` e `batch-upload-service`. O transporte atual de lote pode levar um arquivo, mas o servidor precisa impor `files.length == 1` para o modo avançado.
- Persistir modo, versão, escolha explícita e reserva junto ao job. Escolher o leitor por job, sem alterar `TEXTRACT_MODE` global durante requisições concorrentes.
- Chamar `AnalyzeDocument` síncrono por página. Fila e polling existentes podem continuar acompanhando o trabalho do produto; isso não requer Textract assíncrono.
- Repor páginas e IDs no contrato comum e usar a mesma interpretação financeira. O caminho padrão e o nativo permanecem na implementação vigente até uma liberação específica.
- Validar os 10 MB por página das APIs síncronas. Dividir internamente páginas de um PDF lógico não exige divisão manual pelo usuário.
- Não chamar texto antes de análise por padrão: AnalyzeDocument já inclui OCR. O experimento fez ambos para comparar.
- Persistir resultado e estado por página, com isolamento de proprietário e modo, e reaproveitar páginas concluídas na retomada.
- APIs síncronas não oferecem o token de idempotência de jobs assíncronos. Uma interrupção entre resposta AWS e persistência deixa custo incerto. Usar orçamento por tentativa e retry limitado; não prometer exatamente uma chamada faturada. O consumo da franquia do usuário deve ser idempotente, separado do custo técnico.

### Compatibilidade e migração após aprovação

O experimento não altera schema. O recurso real exige persistir intenção e reserva. Hoje o repositório reconstrói `DocumentPreflightResult` com `**preflight_payload`; chaves arbitrárias nesse JSON quebrariam leitores antigos.

Proposta: coluna aditiva `ocr_mode` com default `standard`, metadados próprios e tabela aditiva de reservas/consumo, com chave idempotente por proprietário, job e operação. Opções de processamento não pertencem ao resultado do preflight.

1. Validar migração aditiva com jobs antigos e código antigo contra o schema expandido; sem DROP, renomeação ou backfill destrutivo.
2. Publicar backend e workers compatíveis, ainda com recurso desligado. Jobs antigos usam `standard`; modo desconhecido falha explicitamente.
3. Confirmar que todos os workers aptos a receber jobs são compatíveis e que execuções antigas terminaram. Só então habilitar escrita de jobs avançados e liberar UI para o piloto pago.
4. Rollback: bloquear novas solicitações, concluir ou cancelar explicitamente jobs avançados e resolver reservas antes de voltar workers antigos. Manter estruturas aditivas e histórico.

Antes de liberar: testar reserva concorrente, esgotamento, falha parcial por página, expiração, proprietário, gratuito, idempotência e retomada. Medir custo por página/modo e recuperação de movimentos; taxa de revisão isolada não mede precisão financeira.

## Custos verificados

Catálogo oficial de `us-east-1`, publicado em 27/10/2026, primeira faixa até um milhão de páginas/mês:

| Operação | US$/página | Quatro páginas |
| --- | ---: | ---: |
| DetectDocumentText síncrono | 0,0015 | 0,0060 |
| AnalyzeDocument, TABLES + LAYOUT | 0,0150 | 0,0600 |

LAYOUT é incluído com TABLES, conforme a página oficial. Não foi solicitado FORMS. Total de referência das oito chamadas: **US$ 0,0660**, excluindo impostos, descontos/franquias AWS e armazenamento. Não é leitura da fatura efetiva.

Fontes: [catálogo regional](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonTextract/current/us-east-1/index.json), [preços e inclusão de LAYOUT](https://aws.amazon.com/textract/pricing/), [limites síncronos](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html), [AnalyzeDocument](https://docs.aws.amazon.com/textract/latest/APIReference/API_AnalyzeDocument.html).

## Reprodução e validação

O avaliador versionado é offline e não faz chamadas pagas:

```powershell
backend\venv\Scripts\python.exe scripts\evaluate_ocr_structure.py `
  --source '<PDF privado>' `
  --text-result '<resposta síncrona de texto recomposta>' `
  --analysis-result '<resposta síncrona de análise recomposta>' `
  --reference '<CSV de conferência visual>' `
  --reference-filename '<nome do PDF no CSV>' `
  --output local/ocr-comparison
```

CSV: `;`, UTF-8, campos `file`, `date` (`dd/mm/aaaa`), `amount` (decimal com ponto e sinal) e `page`. Respostas combinadas: `Blocks` e `DocumentMetadata.Pages`, com IDs/Relationships exclusivos por página física. A referência só avalia resultados; nenhum valor dela entra no reconstrutor ou no interpretador.

Testes sintéticos: palavras/células/outro fornecedor, inclinação e ordem embaralhada, repetições, tabela incompleta, sinais fragmentados/conflitantes, data/valor ilegíveis, falta de geometria, cabeçalhos ausentes, página posterior ilegível, baixa confiança e múltiplos blocos.

Validação final:

- TDD: a primeira execução falhou pela ausência dos módulos; dois testes adicionais falharam pelos controles de baixa confiança e primeira página sem cabeçalho antes das correções. Os 21 cenários novos passaram depois da implementação.
- Suíte completa: `backend/venv/Scripts/python.exe -m pytest backend/tests -q -n 4 --dist=worksteal --tb=short --basetemp C:\Users\erica\AppData\Local\Temp\gettdone-pytest-ocr-shared-sync-full`: **3873 passed, 6 skipped, 2 xfailed**, em **215,05 s**; somente avisos de depreciação FastAPI já existentes.
- Ruff sobre backend e avaliador: `All checks passed!`; lint de texto UTF-8/mojibake e navegação do frontend: aprovados.
- Reprodução offline com os dois OCRs reais: os dois caminhos compartilhados recuperam 72/72, sem divergência de data/valor/página, sem extras e com saldos impressos conferidos; revisão por baixa confiança preservada.
- HTTP/API, frontend e conciliação por transferência/estorno não receberam alterações nesta prova. Nenhuma ativação ou implantação integra esta PR experimental.
